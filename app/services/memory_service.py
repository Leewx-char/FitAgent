"""管理用户可控记忆和确定性的会话状态。

原始消息始终是事实来源。本模块刻意派生两类相互独立的数据：仅从*用户*消息中提取的
短期会话状态，以及只有在用户明确确认后才会向教练暴露的跨会话事实。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.models import MemoryFact, Message, SessionSummary
from app.services.session_facts import extract_session_facts


# 对话上下文按最近 10 轮（每轮 user + assistant）保留，摘要只覆盖窗口外历史。
RECENT_MESSAGE_LIMIT = 20


@dataclass(frozen=True)
class MemoryCandidate:
    fact_key: str
    category: str
    value: dict[str, Any]
    display_text: str
    expires_at: datetime | None


_CANDIDATE_CONFIG = {
    "city": ("location", "所在城市：{value}", 90),
    "training_goal": ("goal", "训练目标：{value}", 90),
    "injuries": ("health_constraint", "需注意的不适/伤病：{value}", 30),
    "diet_pref": ("diet", "饮食偏好：{value}", 60),
}

_PERSONAL_QUERY_TERMS = (
    "我的",
    "我自己",
    "结合我",
    "根据我",
    "给我",
    "计划",
    "训练",
    "恢复",
    "伤",
    "疼",
    "饮食",
    "目标",
    "体重",
    "体检",
)


class MemoryService:
    """Keep the write, retrieval, confirmation, and expiry rules in one testable service."""

    def extract_candidates(self, message: Message) -> list[MemoryCandidate]:
        """Create review-only candidates from an explicit user message.

        The extractor is intentionally deterministic at this stage. A later schema-bound LLM
        extractor can add candidates, but may never directly create confirmed facts.
        """

        if message.role != "user":
            return []
        facts = extract_session_facts([{"role": "user", "content": message.content}])
        candidates = []
        for key, raw_value in facts.items():
            config = _CANDIDATE_CONFIG.get(key)
            if config is None:
                continue
            category, label, expires_in_days = config
            value = {"value": raw_value}
            rendered = "、".join(raw_value) if isinstance(raw_value, list) else str(raw_value)
            candidates.append(
                MemoryCandidate(
                    fact_key=key,
                    category=category,
                    value=value,
                    display_text=label.format(value=rendered),
                    expires_at=datetime.now() + timedelta(days=expires_in_days),
                )
            )
        return candidates

    def propose_from_user_message(
        self, db: DBSession, *, user_id: int, message: Message
    ) -> list[MemoryFact]:
        """Persist de-duplicated proposals without making them available to the Agent."""

        proposals = []
        for candidate in self.extract_candidates(message):
            existing = (
                db.query(MemoryFact)
                .filter(
                    MemoryFact.user_id == user_id,
                    MemoryFact.fact_key == candidate.fact_key,
                    MemoryFact.status.in_(("proposed", "confirmed")),
                )
                .order_by(MemoryFact.updated_at.desc())
                .first()
            )
            encoded_value = json.dumps(candidate.value, ensure_ascii=False, sort_keys=True)
            if existing and existing.value == encoded_value:
                continue
            proposal = MemoryFact(
                id=uuid.uuid4().hex,
                user_id=user_id,
                source_message_id=message.id,
                supersedes_id=existing.id if existing and existing.status == "confirmed" else None,
                fact_key=candidate.fact_key,
                category=candidate.category,
                value=encoded_value,
                display_text=candidate.display_text,
                status="proposed",
                expires_at=candidate.expires_at,
            )
            db.add(proposal)
            proposals.append(proposal)
        return proposals

    @staticmethod
    def list_for_user(
        db: DBSession, *, user_id: int, include_revoked: bool = False
    ) -> list[MemoryFact]:
        query = db.query(MemoryFact).filter(MemoryFact.user_id == user_id)
        if not include_revoked:
            query = query.filter(MemoryFact.status != "revoked")
        return query.order_by(MemoryFact.updated_at.desc()).all()

    @staticmethod
    def confirm(db: DBSession, memory: MemoryFact) -> MemoryFact:
        """Confirm a proposal and revoke the conflicting fact it explicitly replaces."""

        if memory.status == "revoked":
            raise ValueError("已撤销的记忆不能直接确认，请重新创建")
        if memory.supersedes_id:
            previous = db.get(MemoryFact, memory.supersedes_id)
            if previous and previous.user_id == memory.user_id and previous.status == "confirmed":
                previous.status = "revoked"
        memory.status = "confirmed"
        return memory

    @staticmethod
    def revoke(memory: MemoryFact) -> MemoryFact:
        memory.status = "revoked"
        return memory

    @staticmethod
    def format_relevant_memories(db: DBSession, *, user_id: int, query: str) -> str:
        """Return a bounded, user-scoped context only when the request is personal in nature."""

        if not any(term in query for term in _PERSONAL_QUERY_TERMS):
            return "当前问题不需要读取长期记忆。"
        now = datetime.now()
        memories = (
            db.query(MemoryFact)
            .filter(
                MemoryFact.user_id == user_id,
                MemoryFact.status == "confirmed",
                or_(MemoryFact.expires_at.is_(None), MemoryFact.expires_at > now),
            )
            .order_by(MemoryFact.updated_at.desc())
            .limit(6)
            .all()
        )
        if not memories:
            return "没有可用的已确认长期记忆。"
        lines = [
            f"- {memory.display_text}（记忆ID={memory.id}，更新于={memory.updated_at.date()}）"
            for memory in memories
        ]
        return "已确认长期记忆（仅供本次个性化建议使用）：\n" + "\n".join(lines)

    def refresh_session_summary(
        self,
        db: DBSession,
        *,
        session_id: str,
        messages: list[Message],
        recent_message_limit: int = RECENT_MESSAGE_LIMIT,
    ) -> str:
        """Persist an auditable state summary for history outside the recent-message window."""

        older_messages = (
            messages[:-recent_message_limit] if len(messages) > recent_message_limit else []
        )
        if not older_messages:
            return ""
        facts = extract_session_facts(
            [{"role": item.role, "content": item.content} for item in older_messages]
        )
        if not facts:
            return ""
        covered_through = older_messages[-1].id
        content = {
            "schema_version": 1,
            "source": "仅由历史用户消息中的确定性规则提取；不是长期记忆，也未自动写入画像。",
            "facts": facts,
        }
        summary = (
            db.query(SessionSummary).filter(SessionSummary.session_id == session_id).one_or_none()
        )
        if summary is None:
            summary = SessionSummary(
                id=uuid.uuid4().hex,
                session_id=session_id,
                content=json.dumps(content, ensure_ascii=False),
                covered_through_message_id=covered_through,
            )
            db.add(summary)
        elif summary.covered_through_message_id != covered_through or summary.content != json.dumps(
            content, ensure_ascii=False
        ):
            summary.content = json.dumps(content, ensure_ascii=False)
            summary.covered_through_message_id = covered_through

        fact_lines = [f"- {key}: {value}" for key, value in facts.items()]
        return (
            "会话暂存状态（来自较早的用户表达；若与最新消息冲突，以最新消息为准）：\n"
            + "\n".join(fact_lines)
        )
