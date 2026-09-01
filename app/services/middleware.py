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
from app.services.agent_tools import _user_context
from app.services.agent_trace import AgentTrace
from app.core.request_context import request_id_var
from app.utils.logger_handler import logger


def _tool_argument_shape(tool_args: object) -> dict[str, str]:
    """仅记录参数名和类型，避免把用户问题、城市等原始内容写入工具日志。"""
    if not isinstance(tool_args, dict):
        return {"_raw": type(tool_args).__name__}
    return {str(key): type(value).__name__ for key, value in tool_args.items()}


def _consume_tool_budget(runtime_ctx: dict) -> tuple[bool, int, int]:
    """消耗一次请求级工具预算，防止模型陷入重复工具调用循环。"""
    limit = int(runtime_ctx.get("tool_call_limit", 6))
    count = int(runtime_ctx.get("tool_call_count", 0)) + 1
    runtime_ctx["tool_call_count"] = count
    return count <= limit, count, limit


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


def _record_trace_event(
    runtime_ctx: dict,
    *,
    tool_name: str,
    argument_shape: dict[str, str],
    status: str,
    elapsed_ms: int,
    detail: str = "",
) -> None:
    """若运行上下文含执行轨迹，则记录一条工具调用事件。"""
    trace = runtime_ctx.get("agent_trace")
    if isinstance(trace, AgentTrace):
        trace.record_tool(
            tool_name=tool_name,
            argument_shape=argument_shape,
            status=status,
            elapsed_ms=elapsed_ms,
            detail=detail,
        )


@wrap_tool_call
def monitor_tool(
    request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]
) -> ToolMessage | Command:
    """执行工具并统一施加预算、审计、轨迹记录和安全失败响应。"""
    runtime_ctx = request.runtime.context or {}
    user_id = runtime_ctx.get("user_id")
    city = runtime_ctx.get("city", "")
    retrieval_history = runtime_ctx.get("retrieval_history", [])

    tool_name = request.tool_call.get("name", "unknown_tool")
    tool_args = request.tool_call.get("args", {})
    argument_shape = _tool_argument_shape(tool_args)
    allowed, tool_call_count, tool_call_limit = _consume_tool_budget(runtime_ctx)
    if not allowed:
        _log_tool_event(
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="budget_exceeded",
            elapsed_ms=0,
            tool_call_count=tool_call_count,
            detail=f"limit={tool_call_limit}",
        )
        _record_trace_event(
            runtime_ctx,
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="budget_exceeded",
            elapsed_ms=0,
            detail=f"limit={tool_call_limit}",
        )
        return ToolMessage(
            content=(
                "本轮工具调用已达到上限。请不要继续调用工具，"
                "应基于已获得的信息给出明确答复，并说明必要的不确定性。"
            ),
            tool_call_id=request.tool_call.get("id", tool_name),
        )

    context_token = _user_context.set(
        {
            "user_id": user_id,
            "city": city,
            "retrieval_history": retrieval_history,
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
        _record_trace_event(
            runtime_ctx,
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="success",
            elapsed_ms=elapsed_ms,
        )
        if tool_name == "rag_summarize":
            # 工具函数在当前上下文记录真实命中的证据；将其转交给流式层，
            # 不依赖解析 LLM 最终回答中的 [证据:N] 文本。
            runtime_ctx["rag_evidence"] = _user_context.get().get("rag_evidence", [])
            runtime_ctx["rag_evidence_emitted"] = False
        if tool_name == "trigger_report":
            request.runtime.context["report"] = True
        return result
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
        _record_trace_event(
            runtime_ctx,
            tool_name=tool_name,
            argument_shape=argument_shape,
            status="error",
            elapsed_ms=elapsed_ms,
            detail="internal_error",
        )
        return ToolMessage(
            content=(
                f"工具“{tool_name}”暂时不可用。请不要暴露内部错误或反复重试；"
                "可以基于已获得的信息继续回答，并建议用户稍后重试。"
            ),
            tool_call_id=request.tool_call.get("id", tool_name),
        )
    finally:
        _user_context.reset(context_token)


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
    is_report = request.runtime.context.get("report", False)
    session_facts = request.runtime.context.get("session_facts", {})
    session_summary = request.runtime.context.get("session_summary", "")

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
