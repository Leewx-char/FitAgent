"""Agent 执行轨迹的 MySQL 仓储。"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from sqlalchemy.orm import Session as DBSession

from app.models import AgentRun, AgentToolCall


class AgentTraceRepository:
    """将官方 Collector 的运行树持久化为本地查询记录。"""

    @staticmethod
    def save(
        db: DBSession,
        collector: RunCollectorCallbackHandler,
        *,
        request_id: str,
        session_id: str,
        user_id: int,
        user_question: str,
        assistant_answer: str,
        status: str,
    ) -> AgentRun:
        """将本次官方运行树投影为会话可查询的本地记录。"""
        root_run = collector.traced_runs[0] if collector.traced_runs else None
        run_id = str(root_run.id).replace("-", "") if root_run is not None else uuid4().hex
        tool_runs = sorted(_collect_tool_runs(collector.traced_runs), key=_start_timestamp)
        run = AgentRun(
            id=run_id,
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            mode="chat",
            status=status,
            elapsed_ms=_elapsed_ms(root_run),
            tool_call_count=len(tool_runs),
            user_question=user_question,
            assistant_answer=assistant_answer,
        )
        db.add(run)
        db.add_all(
            [
                AgentToolCall(
                    agent_run_id=run_id,
                    sequence=sequence,
                    tool_name=item.name,
                    tool_input=_serialize(item.inputs),
                    tool_output=_serialize(
                        item.error if item.error else _tool_output_value(item.outputs)
                    ),
                    status="failed" if item.error else "succeeded",
                    elapsed_ms=_elapsed_ms(item),
                )
                for sequence, item in enumerate(tool_runs, start=1)
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


def _collect_tool_runs(runs: list) -> list:
    """递归提取工具运行及带 agent_tool 标签的运行。"""
    collected = []
    for run in runs:
        if run.run_type == "tool" or "agent_tool" in (run.tags or []):
            collected.append(run)
        collected.extend(_collect_tool_runs(run.child_runs or []))
    return collected


def _serialize(value: object) -> str:
    """以 UTF-8 JSON 形式序列化 Collector 字段。"""
    return json.dumps(value, ensure_ascii=False, default=str)


def _tool_output_value(outputs: object) -> object:
    """展开官方工具回调包装的单个 output 字段。"""
    if isinstance(outputs, dict) and set(outputs) == {"output"}:
        return outputs["output"]
    return outputs


def _elapsed_ms(run: object | None) -> int:
    """计算运行起止时间的毫秒差，缺失时返回零。"""
    if run is None or run.start_time is None or run.end_time is None:
        return 0
    return max(0, round((run.end_time - run.start_time).total_seconds() * 1000))


def _start_timestamp(run: object) -> float:
    """提供可排序的开始时间戳，缺失时间排在最前。"""
    start_time: datetime | None = run.start_time
    return start_time.timestamp() if start_time is not None else 0
