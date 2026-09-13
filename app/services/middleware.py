import json
import time
from collections.abc import Awaitable, Callable

from app.utils.prompt_loader import load_system_prompts, load_report_prompts
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, before_model, dynamic_prompt
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from app.core.request_context import request_id_var
from app.utils.chat_latency import ChatLatencyTracker
from app.utils.logger_handler import logger


def _tool_argument_shape(tool_args: object) -> dict[str, str]:
    """仅记录参数名和类型，避免把用户问题、城市等原始内容写入工具日志。"""
    if not isinstance(tool_args, dict):
        return {"_raw": type(tool_args).__name__}
    return {str(key): type(value).__name__ for key, value in tool_args.items()}


def _log_tool_event(
    *,
    tool_name: str,
    argument_shape: dict[str, str],
    status: str,
    elapsed_ms: int,
    detail: str = "",
) -> None:
    """输出可关联请求、但不泄露原始参数和异常文本的结构化工具审计事件。"""
    event = {
        "request_id": request_id_var.get(),
        "tool": tool_name,
        "argument_shape": argument_shape,
        "status": status,
        "elapsed_ms": elapsed_ms,
    }
    if detail:
        event["detail"] = detail
    logger.info("AGENT_TOOL_CALL %s", json.dumps(event, ensure_ascii=False))


def _record_tool_latency(
    request: ToolCallRequest, *, tool_name: str, status: str, elapsed_ms: int
) -> None:
    """将工具耗时写入请求级链路日志，不记录工具参数值。"""
    dependencies = getattr(request.runtime.context, "dependencies", None)
    timing = getattr(dependencies, "latency_tracker", None)
    if isinstance(timing, ChatLatencyTracker):
        timing.record_duration(
            "agent.tool_completed",
            elapsed_ms,
            tool=tool_name,
            status=status,
        )


def _tool_monitoring_context(request: ToolCallRequest) -> tuple[str, dict[str, str], float]:
    """提取一次工具调用的安全审计字段和计时起点。"""
    tool_name = request.tool_call.get("name", "unknown_tool")
    tool_args = request.tool_call.get("args", {})
    return tool_name, _tool_argument_shape(tool_args), time.perf_counter()


def _record_tool_outcome(
    request: ToolCallRequest,
    *,
    tool_name: str,
    argument_shape: dict[str, str],
    started_at: float,
    status: str,
) -> None:
    """统一记录工具成功或失败的审计事件和请求级耗时。"""
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    _log_tool_event(
        tool_name=tool_name,
        argument_shape=argument_shape,
        status=status,
        elapsed_ms=elapsed_ms,
        detail="internal_error" if status == "error" else "",
    )
    _record_tool_latency(request, tool_name=tool_name, status=status, elapsed_ms=elapsed_ms)


def _tool_failure_response(request: ToolCallRequest, tool_name: str) -> Command:
    """将工具内部异常转换为可继续执行的安全工具消息。"""
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=(
                        f"工具“{tool_name}”暂时不可用。请不要暴露内部错误或反复重试；"
                        "可以基于已获得的信息继续回答，并建议用户稍后重试。"
                    ),
                    tool_call_id=request.tool_call.get("id", tool_name),
                )
            ],
        }
    )


class ToolMonitoringMiddleware(AgentMiddleware):
    """同时覆盖同步和异步工具调用的审计与安全降级。"""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """在同步 Agent 执行时记录工具结果并隔离工具异常。"""
        tool_name, argument_shape, started_at = _tool_monitoring_context(request)

        try:
            result = handler(request)
        except Exception:
            logger.exception("Agent 工具执行异常：%s", tool_name)
            _record_tool_outcome(
                request,
                tool_name=tool_name,
                argument_shape=argument_shape,
                started_at=started_at,
                status="error",
            )
            return _tool_failure_response(request, tool_name)
        else:
            _record_tool_outcome(
                request,
                tool_name=tool_name,
                argument_shape=argument_shape,
                started_at=started_at,
                status="success",
            )
            return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """在异步流式 Agent 执行时复用同步路径的审计与降级语义。"""
        tool_name, argument_shape, started_at = _tool_monitoring_context(request)

        try:
            result = await handler(request)
        except Exception:
            logger.exception("Agent 工具执行异常：%s", tool_name)
            _record_tool_outcome(
                request,
                tool_name=tool_name,
                argument_shape=argument_shape,
                started_at=started_at,
                status="error",
            )
            return _tool_failure_response(request, tool_name)
        else:
            _record_tool_outcome(
                request,
                tool_name=tool_name,
                argument_shape=argument_shape,
                started_at=started_at,
                status="success",
            )
            return result


monitor_tool = ToolMonitoringMiddleware()


@before_model
def log_before_model(
    state: AgentState,  # 整个Agent智能体中的状态记录
    runtime: Runtime,  # 记录了整个执行过程的上下文信息
):  # 在模型执行前输出日志
    """在模型执行前记录消息数量与最后一条消息的类型。"""
    logger.info(f"[log_before_model]即将调用模型，带有{len(state['messages'])}条消息。")

    last_message = state["messages"][-1]
    content = getattr(last_message, "content", "")
    logger.debug(
        "[log_before_model]last_message_type=%s content_length=%s",
        type(last_message).__name__,
        len(str(content)),
    )

    return None


@dynamic_prompt
def report_prompt_switch(request: ModelRequest):
    """Choose only the report or normal static prompt for this model call."""
    if request.state.get("report", False):
        return load_report_prompts()
    return load_system_prompts()
