import json
import time
from collections.abc import MutableMapping
from typing import Callable
from app.utils.prompt_loader import load_system_prompts, load_report_prompts
from langchain.agents import AgentState
from langchain.agents.middleware import wrap_tool_call, before_model, dynamic_prompt, ModelRequest
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from app.core.request_context import request_id_var
from app.utils.logger_handler import logger


def _tool_argument_shape(tool_args: object) -> dict[str, str]:
    """仅记录参数名和类型，避免把用户问题、城市等原始内容写入工具日志。"""
    if not isinstance(tool_args, dict):
        return {"_raw": type(tool_args).__name__}
    return {str(key): type(value).__name__ for key, value in tool_args.items()}


def _consume_tool_budget(
    state: MutableMapping[str, object], *, limit: int, tool_call_position: int = 0
) -> tuple[bool, int, int]:
    """以同批工具调用的位置计算稳定的预算序号。"""
    count = int(state.get("tool_call_count", 0)) + tool_call_position + 1
    state["tool_call_count"] = count
    return count <= limit, count, limit


def _tool_call_position(request: ToolCallRequest) -> int:
    """返回当前工具在最后一条 AIMessage 工具调用列表中的位置。"""
    messages = request.state.get("messages", [])
    tool_calls = getattr(messages[-1], "tool_calls", []) if messages else []
    for position, tool_call in enumerate(tool_calls):
        if tool_call.get("id") == request.tool_call.get("id"):
            return position
    return 0


def _tool_call_limit(request: ToolCallRequest) -> int:
    """优先读取内层状态预算，缺失时使用请求执行器配置的上限。"""
    limit = request.state.get("tool_call_limit")
    if limit is not None:
        return int(limit)
    dependencies = request.runtime.context.dependencies
    limit = getattr(dependencies, "max_tool_calls", None)
    if limit is None:
        limit = getattr(dependencies.personalized_agent_executor, "max_tool_calls", 6)
    return int(limit)


def _log_tool_event(
    *,
    tool_name: str,
    argument_shape: dict[str, str],
    status: str,
    elapsed_ms: int,
    tool_call_count: int,
    detail: str = "",
) -> None:
    """输出可关联请求、但不泄露原始参数和异常文本的结构化工具审计事件。"""
    event = {
        "request_id": request_id_var.get(),
        "tool": tool_name,
        "argument_shape": argument_shape,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "tool_call_count": tool_call_count,
    }
    if detail:
        event["detail"] = detail
    logger.info("AGENT_TOOL_CALL %s", json.dumps(event, ensure_ascii=False))


@wrap_tool_call
def monitor_tool(
    request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]
) -> ToolMessage | Command:
    """执行工具并统一施加预算、审计和安全失败响应。"""
    tool_name = request.tool_call.get("name", "unknown_tool")
    tool_args = request.tool_call.get("args", {})
    argument_shape = _tool_argument_shape(tool_args)
    limit = _tool_call_limit(request)
    allowed, tool_call_count, tool_call_limit = _consume_tool_budget(
        request.state,
        limit=limit,
        tool_call_position=_tool_call_position(request),
    )
    if not allowed:
        _log_tool_event(
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="budget_exceeded",
            elapsed_ms=0,
            tool_call_count=tool_call_count,
            detail=f"limit={tool_call_limit}",
        )
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            "本轮工具调用已达到上限。请不要继续调用工具，"
                            "应基于已获得的信息给出明确答复，并说明必要的不确定性。"
                        ),
                        tool_call_id=request.tool_call.get("id", tool_name),
                    )
                ],
                "tool_call_count": tool_call_count,
            }
        )

    started_at = time.perf_counter()

    try:
        result = handler(request)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        _log_tool_event(
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="success",
            elapsed_ms=elapsed_ms,
            tool_call_count=tool_call_count,
        )
        return _with_tool_call_count(result, tool_call_count, request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        logger.exception("Agent 工具执行异常：%s", tool_name)
        _log_tool_event(
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="error",
            elapsed_ms=elapsed_ms,
            tool_call_count=tool_call_count,
            detail="internal_error",
        )
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
                "tool_call_count": tool_call_count,
            }
        )


def _with_tool_call_count(
    result: ToolMessage | Command, tool_call_count: int, request: ToolCallRequest
) -> Command:
    """把中间件预算计数与工具原有的状态更新合并返回。"""
    if isinstance(result, Command):
        return Command(update={**result.update, "tool_call_count": tool_call_count})
    return Command(update={"messages": [result], "tool_call_count": tool_call_count})


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


@dynamic_prompt  # 每一次在生成提示词之前，调用此函数
def report_prompt_switch(request: ModelRequest):  # 动态切换提示词
    """依据报告模式与可信会话事实选择并补全系统提示词。"""
    is_report = request.state.get("report", False)
    session_facts = request.state.get("session_facts", {})
    session_summary = request.state.get("session_summary", "")

    facts_prompt = ""
    if session_facts:
        fact_lines = [f"- {key}: {value}" for key, value in session_facts.items()]
        facts_prompt = (
            "\n\n已知会话事实：\n"
            + "\n".join(fact_lines)
            + "\n请优先使用这些历史事实回答，不要忽略用户之前已经明确提到的信息。"
        )

    if session_summary:
        facts_prompt += (
            "\n\n"
            + session_summary
            + "\n这是短期会话状态，不是已确认的跨会话记忆；不能据此声称用户已授权保存。"
        )

    if is_report:  # 是报告生成场景，返回报告生成提示词内容
        return load_report_prompts() + facts_prompt

    return load_system_prompts() + facts_prompt
