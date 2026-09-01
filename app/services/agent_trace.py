"""Agent 执行轨迹的内存聚合模型。

轨迹只保留工具名、参数类型、状态与耗时；用户消息、工具参数值和模型回复都不进入该对象。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app.core.request_context import request_id_var


@dataclass(frozen=True)
class AgentToolTrace:
    sequence: int
    tool_name: str
    argument_shape: dict[str, str]
    status: str
    elapsed_ms: int
    detail: str = ""


@dataclass
class AgentTrace:
    """贯穿一轮聊天请求的可持久化、安全执行摘要。"""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    request_id: str = field(default_factory=request_id_var.get)
    mode: str = "agent"
    status: str = "running"
    _started_at: float = field(default_factory=time.perf_counter, repr=False)
    _elapsed_ms: int = 0
    tool_calls: list[AgentToolTrace] = field(default_factory=list)

    @property
    def elapsed_ms(self) -> int:
        """返回已结束耗时或当前运行耗时的毫秒数。"""
        return self._elapsed_ms or round((time.perf_counter() - self._started_at) * 1000)

    def record_tool(
        self,
        *,
        tool_name: str,
        argument_shape: dict[str, str],
        status: str,
        elapsed_ms: int,
        detail: str = "",
    ) -> None:
        """追加一条仅含安全元数据的工具调用轨迹。"""
        self.tool_calls.append(
            AgentToolTrace(
                sequence=len(self.tool_calls) + 1,
                tool_name=tool_name,
                argument_shape=argument_shape,
                status=status,
                elapsed_ms=elapsed_ms,
                detail=detail,
            )
        )

    def finish(self, status: str) -> None:
        """标记执行结束状态并冻结累计耗时。"""
        self.status = status
        self._elapsed_ms = round((time.perf_counter() - self._started_at) * 1000)
