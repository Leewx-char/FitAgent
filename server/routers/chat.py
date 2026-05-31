"""
 对话路由 —— 聊天消息的接收和 SSE 流式响应。

 POST /api/chat：接收用户消息和会话 ID，调用 Agent 的 execute_stream，
                 通过 Server-Sent Events 逐块推回前端。

 SSE 格式：每块数据以 "data: <文本>\n\n" 发送，前端 EventSource 自动解析。
 """
import asyncio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession
from server.schemas import ChatRequest
from server.deps import get_db, get_agent
from server.auth import get_current_user
from server.models import Session as SessionModel, Message, User
from agent.react_agent import ReactAgent
from agent.tools.agent_tools import _user_context
import uuid

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def sse_generator(
    agent: ReactAgent,
    messages: list[dict],
    db: DBSession,
    session_id: str,
    user_message: str,
    current_user: User,
):
    # 获取当前事件循环
    loop = asyncio.get_event_loop()
    full_response = ""
    # 用哨兵值代替 StopIteration，避免 run_in_executor 报错
    _SENTINEL = object()
    # 调 next(gen) 取下一块，如果生成器结束了（抛出 StopIteration），返回哨兵而不是让异常冒泡
    def _next_chunk(gen):
        try:
            return next(gen)
        except StopIteration:
            return _SENTINEL
    # 把 gen 创建为能传递 user_id/city 的闭包
    session_facts = ReactAgent._extract_session_facts(messages)
    user_id = current_user.id
    city = session_facts.get("city", "") or ""
    gen = iter(agent.execute_stream(messages, user_id=user_id, city=city))
    while True:
        # 在线程池里执行 _next_chunk(gen)，因为 agent.execute_stream 是同步的，不能阻塞主线程，服务其他用户
        chunk = await loop.run_in_executor(None, _next_chunk, gen)
        if chunk is _SENTINEL:
            break
        full_response += chunk
        yield f"data: {chunk}\n\n"
    # 流式结束后：存 assistant 消息到数据库
    db.add(Message(
        session_id=session_id,
        role="assistant",
        content=full_response.strip(),
    ))
    # 如果是第一条消息，自动更新会话标题
    session = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.user_id == current_user.id,
    ).first()
    if session and session.title == "新对话":
        session.title = user_message[:24] + ("..." if len(user_message) > 24 else "")
    db.commit()
    yield "data: [DONE]\n\n"

@router.post("")
async def chat(
    request: ChatRequest,
    db: DBSession = Depends(get_db),
    agent: ReactAgent = Depends(get_agent),
    current_user: User = Depends(get_current_user),
):
    # 1. 如果没有 session_id，自动创建新会话
    if request.session_id:
        session = (
            db.query(SessionModel)
            .filter(SessionModel.id == request.session_id, SessionModel.user_id == current_user.id)
            .first()
        )
        if not session:
            return {"code": 404, "message": "会话不存在", "data": None}
        session_id = session.id
    else:
        session_id = uuid.uuid4().hex[:32]
        new_session = SessionModel(
            id=session_id,
            title="新对话",
            user_id=current_user.id,
        )
        db.add(new_session)
        db.commit()
    # 2. 存用户消息到数据库
    db.add(Message(
        session_id=session_id,
        role="user",
        content=request.message,
    ))
    db.commit()
    # 3. 从数据库加载历史消息，拼接新消息
    history_messages = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at)
        .all()
    )
    messages = [{"role": m.role, "content": m.content} for m in history_messages]
    # 从对话中提取城市等上下文信息
    session_facts = ReactAgent._extract_session_facts(messages)
    _user_context.set({"user_id": current_user.id, "city": session_facts.get("city", "") or ""})
    # 4. 流式响应
    return StreamingResponse(
        sse_generator(agent, messages, db, session_id, request.message, current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-Id": session_id,
        },
    )