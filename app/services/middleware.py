import json
import time
from typing import Callable
from app.utils.prompt_loader import load_system_prompts, load_report_prompts
from langchain.agents import AgentState
from langchain.agents.middleware import wrap_tool_call, before_model, dynamic_prompt, ModelRequest
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


@wrap_tool_call
def monitor_tool(
    request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]
) -> ToolMessage | Command:
    """执行工具并统一施加审计和安全失败响应。"""
    tool_name = request.tool_call.get("name", "unknown_tool")
    tool_args = request.tool_call.get("args", {})
    argument_shape = _tool_argument_shape(tool_args)
    started_at = time.perf_counter()

    try:
        result = handler(request)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        _log_tool_event(
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="success",
            elapsed_ms=elapsed_ms,
        )
        _record_tool_latency(request, tool_name=tool_name, status="success", elapsed_ms=elapsed_ms)
        return result
    except Exception:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        logger.exception("Agent 工具执行异常：%s", tool_name)
        _log_tool_event(
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="error",
            elapsed_ms=elapsed_ms,
            detail="internal_error",
        )
        _record_tool_latency(request, tool_name=tool_name, status="error", elapsed_ms=elapsed_ms)
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
