"""
对话路由 —— 聊天消息的接收和 SSE 流式响应。

POST /api/chat：接收用户消息和会话 ID，调用 Agent 的 execute_stream，
                通过 Server-Sent Events 逐块推回前端。

SSE 格式：每块数据以 "data: <文本>\n\n" 发送，前端 EventSource 自动解析。
"""

import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session as DBSession
from app.schemas import ChatRequest
from app.core.deps import get_db, get_agent
from app.core.auth import get_current_user, decode_access_token
from app.core.request_context import request_id_var
from app.models import Session as SessionModel, Message, User
from app.services.react_agent import ReactAgent
from app.services.memory_service import MemoryService, RECENT_MESSAGE_LIMIT
from app.repositories.agent_trace_repository import AgentTraceRepository
from app.core.database import get_db_session
from app.utils.logger_handler import logger
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
import uuid
import re

# 敏感信息脱敏正则
_SENSITIVE_PATTERNS = [
    (re.compile(r"1[3-9]\d{9}"), "[手机号已隐藏]"),
    (re.compile(r"\d{17}[\dXx]"), "[身份证号已隐藏]"),
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[邮箱已隐藏]"),
]


def _redact_sensitive(text: str) -> str:
    """替换文本中的手机号、身份证号和邮箱等敏感信息。"""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


router = APIRouter(prefix="/api/chat", tags=["chat"])


def _rate_limit_key(request: Request) -> str:
    """按登录用户限流：从 Authorization 头解出 user_id 作为限流键。
    无有效 token 时回退到客户端 IP（防御性，chat 实际要求登录）。
    这样同一 IP 下的多个用户各自独立计数，单用户也无法靠换 IP 绕过。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        payload = decode_access_token(auth[7:])
        if payload and payload.get("user_id"):
            return f"user:{payload['user_id']}"
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)


async def sse_generator(
    agent: ReactAgent,
    messages: list[dict],
    db: DBSession,
    session_id: str,
    user_message: str,
    current_user: User,
    session_summary: str = "",
):
    """执行 Agent 流式响应，转发 SSE 事件并保存回答与执行轨迹。"""
    # 获取当前事件循环
    loop = asyncio.get_event_loop()
    full_response = ""
    # 用哨兵值代替 StopIteration，避免 run_in_executor 报错
    _SENTINEL = object()

    # 调 next(gen) 取下一块，如果生成器结束了（抛出 StopIteration），返回哨兵而不是让异常冒泡
    def _next_chunk(gen):
        """读取生成器下一块内容，并以哨兵表示正常结束。"""
        try:
            return next(gen)
        except StopIteration:
            return _SENTINEL

    # 服务层从图状态提取会话事实，HTTP 层仅传递身份与稳定会话标识。
    user_id = current_user.id
    collector = RunCollectorCallbackHandler()
    gen = iter(
        agent.execute_stream(
            messages,
            user_id=user_id,
            session_id=session_id,
            session_summary=session_summary,
            config={"callbacks": [collector]},
        )
    )
    stream_failed = False
    try:
        while True:
            chunk = await loop.run_in_executor(None, _next_chunk, gen)
            if chunk is _SENTINEL:
                break
            chunk = chunk.strip()
            if not chunk:
                continue
            # 解析 agent 发来的 JSON 事件
            try:
                event = json.loads(chunk)
            except (json.JSONDecodeError, TypeError):
                # 不是 JSON（兜底），当纯文本处理
                chunk = _redact_sensitive(chunk)
                full_response += chunk
                payload = {"type": "text", "content": chunk}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                continue
            if event.get("type") == "tool":
                # 工具调用通知：把英文名翻译成中文显示给用户
                payload = {"type": "tool", "name": event.get("name", "")}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            elif event.get("type") == "evidence":
                # 证据卡片属于检索结果的一部分，原样转发给前端与回答中的 [证据:N] 对应。
                payload = {
                    "type": "evidence",
                    "items": event.get("items", []),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            elif event.get("type") == "text":
                # 文本增量：只有新增的部分
                content = _redact_sensitive(event.get("content", ""))
                full_response += content
                payload = {"type": "text", "content": content}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    except Exception as e:
        stream_failed = True
        # 捕获所有异常（API Key 无效、额度不足、超时等），给用户友好的中文提示
        logger.error(f"Agent流式响应异常：{str(e)}", exc_info=True)  # ← 加这行
        error_msg = str(e)
        if "apiKey" in error_msg or "InvalidApiKey" in error_msg or "api_key" in error_msg.lower():
            error_msg = "AI 服务配置错误，请联系管理员"
        elif (
            "quota" in error_msg.lower()
            or "balance" in error_msg.lower()
            or "limit" in error_msg.lower()
        ):
            error_msg = "AI 服务额度不足，请联系管理员"
        elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            error_msg = "AI 服务响应超时，请稍后重试"
        else:
            error_msg = "服务暂时不可用，请稍后重试"
        yield f"data: {json.dumps({'type': 'error', 'content': error_msg}, ensure_ascii=False)}\n\n"
    try:
        with get_db_session() as trace_db:
            AgentTraceRepository.save(
                trace_db,
                collector,
                request_id=request_id_var.get(),
                session_id=session_id,
                user_id=current_user.id,
                user_question=user_message,
                assistant_answer=full_response.strip(),
                status="failed" if stream_failed else "succeeded",
            )
    except Exception:
        # 轨迹表尚未迁移或单独写入失败时，不得影响用户已经得到的流式回答。
        logger.exception("Agent 执行轨迹写入失败：request_id=%s", request_id_var.get())
    # 流式结束后：存 assistant 消息到数据库
    db.add(
        Message(
            session_id=session_id,
            role="assistant",
            content=full_response.strip() if full_response.strip() else "（回复异常）",
        )
    )
    # 如果是第一条消息，自动更新会话标题
    session = (
        db.query(SessionModel)
        .filter(
            SessionModel.id == session_id,
            SessionModel.user_id == current_user.id,
        )
        .first()
    )
    if session and session.title == "新对话":
        session.title = user_message[:24] + ("..." if len(user_message) > 24 else "")
    db.commit()
    yield "data: [DONE]\n\n"


@router.post("")
@limiter.limit("20/minute")
async def chat(
    request: Request,
    payload: ChatRequest,
    db: DBSession = Depends(get_db),
    agent: ReactAgent = Depends(get_agent),
    current_user: User = Depends(get_current_user),
):
    """保存用户消息与短期会话状态，并返回 Agent 的 SSE 响应流。"""
    # 0. 输入校验：防 token 炸弹 + 空消息
    if not payload.message or not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    if len(payload.message) > 4000:
        raise HTTPException(
            status_code=400,
            detail=f"消息过长（{len(payload.message)}字符），请精简后重试（上限4000字符）",
        )

    # 1. 如果没有 session_id，自动创建新会话
    if payload.session_id:
        session = (
            db.query(SessionModel)
            .filter(SessionModel.id == payload.session_id, SessionModel.user_id == current_user.id)
            .first()
        )
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
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
    user_message_record = Message(session_id=session_id, role="user", content=payload.message)
    db.add(user_message_record)
    db.commit()
    db.refresh(user_message_record)

    # 只从本条用户消息生成“待确认”候选；模型回答无法进入这一写入链路。
    MemoryService().propose_from_user_message(
        db,
        user_id=current_user.id,
        message=user_message_record,
    )
    db.commit()
    # 3. 从数据库加载历史消息，拼接新消息
    history_messages = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at, Message.id)
        .all()
    )
    all_messages = [{"role": m.role, "content": m.content} for m in history_messages]
    # 最近 10 轮（20 条）原文 + 可审计短期状态，避免全量历史进入模型上下文。
    recent_message_limit = RECENT_MESSAGE_LIMIT
    session_summary = MemoryService().refresh_session_summary(
        db,
        session_id=session_id,
        messages=history_messages,
        recent_message_limit=recent_message_limit,
    )
    db.commit()
    messages = all_messages[-recent_message_limit:]
    # 4. 流式响应
    return StreamingResponse(
        sse_generator(
            agent,
            messages,
            db,
            session_id,
            payload.message,
            current_user,
            session_summary,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )
