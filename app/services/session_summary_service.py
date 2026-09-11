"""On-demand task-oriented cache for early stored session messages."""

import json
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session as DBSession

from app.models import Message, Session, SessionSummary


RECENT_AGENT_MESSAGE_LIMIT = 20
MAX_SUMMARY_CHARS = 2400
SUMMARY_SCHEMA_VERSION = 3
SOURCE = "压缩早期已存储消息作为任务上下文；不是长期记忆，也不会自动写入用户画像或 mem0。"
WINDOW_COVERED_MESSAGE = "当前可见对话已覆盖会话，无需读取早期摘要。"
SUMMARY_SYSTEM_PROMPT = """将以下早期已存储消息压缩为任务导向的摘要。
仅整理任务相关信息，不给建议。输出不超过 2400 个字符，且只使用以下四个标题：
当前任务目标
已完成工作/决策
关键发现/约束
未解决事项"""
SUMMARY_PREFIX = "早期会话摘要：\n"
HUMAN_PAYLOAD_PREFIX = "已有摘要（可为空）：\n"
HUMAN_PAYLOAD_SUFFIX = "\n\n新增早期已存储消息：\n"
PRIOR_SUMMARY_BUDGET = MAX_SUMMARY_CHARS // 2
SOURCE_TEXT_BUDGET = (
    MAX_SUMMARY_CHARS - PRIOR_SUMMARY_BUDGET - len(HUMAN_PAYLOAD_PREFIX) - len(HUMAN_PAYLOAD_SUFFIX)
)


class SessionSummaryNotFoundError(LookupError):
    """The requested session does not belong to the user."""


class SessionSummaryService:
    def __init__(self, model):
        self.model = model

    def get_summary(self, db: DBSession, user_id: int, session_id: str) -> str:
        session = (
            db.query(Session)
            .filter(Session.id == session_id, Session.user_id == user_id)
            .one_or_none()
        )
        if session is None:
            raise SessionSummaryNotFoundError(session_id)

        messages = (
            db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.created_at, Message.id)
            .all()
        )
        older_messages = (
            messages[:-RECENT_AGENT_MESSAGE_LIMIT]
            if len(messages) > RECENT_AGENT_MESSAGE_LIMIT
            else []
        )
        if not older_messages:
            return WINDOW_COVERED_MESSAGE

        boundary = older_messages[-1].id
        row = db.query(SessionSummary).filter(SessionSummary.session_id == session_id).one_or_none()
        cached_summary = self._valid_summary(row.content) if row is not None else None
        if (
            row is not None
            and cached_summary is not None
            and row.covered_through_message_id == boundary
        ):
            return self._format(cached_summary)

        covered = row.covered_through_message_id if row is not None and cached_summary else 0
        summary = cached_summary or ""
        pending_messages = [
            (message.id, message.content) for message in older_messages if message.id > covered
        ]
        while pending_messages:
            chunk, pending_messages = self._take_chunk(summary, pending_messages)
            summary = self._invoke(summary, chunk)

        content = json.dumps(
            {
                "schema_version": SUMMARY_SCHEMA_VERSION,
                "source": SOURCE,
                "summary": summary,
            },
            ensure_ascii=False,
        )
        if row is None:
            row = SessionSummary(
                id=uuid.uuid4().hex,
                session_id=session_id,
                content=content,
                covered_through_message_id=boundary,
            )
            db.add(row)
        else:
            row.content = content
            row.covered_through_message_id = boundary
        db.flush()
        return self._format(summary)

    @staticmethod
    def _valid_summary(content: str) -> str | None:
        try:
            parsed = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(parsed, dict)
            or type(parsed.get("schema_version")) is not int
            or parsed["schema_version"] != SUMMARY_SCHEMA_VERSION
            or not isinstance(parsed.get("summary"), str)
            or not parsed["summary"].strip()
        ):
            return None
        return parsed["summary"][:MAX_SUMMARY_CHARS]

    @classmethod
    def _human_payload(cls, prior_summary: str, numbered_messages: str) -> str:
        return (
            f"{HUMAN_PAYLOAD_PREFIX}{prior_summary[:PRIOR_SUMMARY_BUDGET]}"
            f"{HUMAN_PAYLOAD_SUFFIX}{numbered_messages}"
        )

    @classmethod
    def _take_chunk(
        cls, prior_summary: str, pending: list[tuple[int, str]]
    ) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
        available = SOURCE_TEXT_BUDGET
        chunk: list[tuple[int, str]] = []
        remaining = list(pending)
        while remaining:
            message_id, text = remaining[0]
            prefix = f"{len(chunk) + 1}. "
            if chunk:
                prefix = "\n" + prefix
            space_for_text = available - len(prefix)
            if space_for_text <= 0:
                break
            fragment = text[:space_for_text]
            chunk.append((message_id, fragment))
            available -= len(prefix) + len(fragment)
            if len(fragment) == len(text):
                remaining.pop(0)
            else:
                remaining[0] = (message_id, text[len(fragment) :])
                break
        return chunk, remaining

    def _invoke(self, prior_summary: str, messages: list[tuple[int, str]]) -> str:
        numbered_messages = "\n".join(
            f"{index}. {text}" for index, (_, text) in enumerate(messages, start=1)
        )
        response = self.model.invoke(
            [
                SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
                HumanMessage(content=self._human_payload(prior_summary, numbered_messages)),
            ]
        )
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("summary model returned empty summary")
        return content.strip()[:MAX_SUMMARY_CHARS]

    @staticmethod
    def _format(summary: str) -> str:
        return SUMMARY_PREFIX + summary[:MAX_SUMMARY_CHARS]
