"""
对话路由 —— 聊天消息的接收和 SSE 流式响应。

POST /api/chat：接收用户消息和会话 ID，调用 Agent 的 execute_stream，
                通过 Server-Sent Events 逐块推回前端。

SSE 格式：每块数据以 "data: <文本>\n\n" 发送，前端 EventSource 自动解析。
"""

import json
import asyncio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session as DBSession
from app.schemas import ChatRequest
from app.core.deps import get_db, get_agent
from app.core.auth import get_current_user, decode_access_token
from app.models import Session as SessionModel, Message, User
from app.services.react_agent import ReactAgent
from app.services.agent_tools import _user_context
from app.utils.logger_handler import logger
import uuid
import re

# 敏感信息脱敏正则
_SENSITIVE_PATTERNS = [
    (re.compile(r"1[3-9]\d{9}"), "[手机号已隐藏]"),
    (re.compile(r"\d{17}[\dXx]"), "[身份证号已隐藏]"),
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[邮箱已隐藏]"),
]


def _redact_sensitive(text: str) -> str:
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
            elif event.get("type") == "text":
                # 文本增量：只有新增的部分
                content = _redact_sensitive(event.get("content", ""))
                full_response += content
                payload = {"type": "text", "content": content}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    except Exception as e:
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
    # 0. 输入校验：防 token 炸弹 + 空消息
    if not payload.message or not payload.message.strip():
        return {"code": 400, "message": "消息不能为空", "data": None}
    if len(payload.message) > 4000:
        return {
            "code": 400,
            "message": f"消息过长（{len(payload.message)}字符），请精简后重试（上限4000字符）",
            "data": None,
        }

    # 1. 如果没有 session_id，自动创建新会话
    if payload.session_id:
        session = (
            db.query(SessionModel)
            .filter(SessionModel.id == payload.session_id, SessionModel.user_id == current_user.id)
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
    db.add(
        Message(
            session_id=session_id,
            role="user",
            content=payload.message,
        )
    )
    db.commit()
    # 3. 从数据库加载历史消息，拼接新消息
    history_messages = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at)
        .all()
    )
    all_messages = [{"role": m.role, "content": m.content} for m in history_messages]
    # 从对话中提取城市等上下文信息（用全量消息，保证提取准确）
    session_facts = ReactAgent._extract_session_facts(all_messages)
    # 滑动窗口：只保留最近 20 轮（40 条消息），防止长对话 token 爆炸
    MAX_ROUNDS = 20
    messages = (
        all_messages[-(MAX_ROUNDS * 2) :] if len(all_messages) > MAX_ROUNDS * 2 else all_messages
    )
    _user_context.set({"user_id": current_user.id, "city": session_facts.get("city", "") or ""})
    # 4. 流式响应
    return StreamingResponse(
        sse_generator(agent, messages, db, session_id, payload.message, current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )
