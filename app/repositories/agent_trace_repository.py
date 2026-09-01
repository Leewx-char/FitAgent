"""Agent 执行轨迹的 MySQL 仓储。"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session as DBSession

from app.models import AgentRun, AgentToolCall
from app.services.agent_trace import AgentTrace


class AgentTraceRepository:
    """只保存无敏感字段的 Agent 运行与工具调用摘要。"""

    @staticmethod
    def save(db: DBSession, trace: AgentTrace, *, session_id: str, user_id: int) -> AgentRun:
        """将一次执行轨迹及其工具调用安全元数据写入数据库。"""
        run = AgentRun(
            id=trace.run_id,
            request_id=trace.request_id,
            session_id=session_id,
            user_id=user_id,
            mode=trace.mode,
            status=trace.status,
            elapsed_ms=trace.elapsed_ms,
            tool_call_count=len(trace.tool_calls),
        )
        db.add(run)
        db.add_all(
            [
                AgentToolCall(
                    agent_run_id=trace.run_id,
                    sequence=item.sequence,
                    tool_name=item.tool_name,
                    argument_shape=json.dumps(item.argument_shape, ensure_ascii=False),
                    status=item.status,
                    elapsed_ms=item.elapsed_ms,
                    detail=item.detail,
                )
                for item in trace.tool_calls
            ]
        )
        return run

    @staticmethod
    def list_for_session(
        db: DBSession, *, session_id: str, user_id: int, limit: int = 20
    ) -> list[AgentRun]:
        """按创建时间倒序查询用户会话的有限执行轨迹。"""
        return (
            db.query(AgentRun)
            .filter(AgentRun.session_id == session_id, AgentRun.user_id == user_id)
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
            .all()
        )
