import asyncio
from operator import add, or_
from types import SimpleNamespace
from typing import Annotated, Any, AsyncIterator, Callable, Iterable, cast
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import merge_configs

from app.services.factory import get_chat_model
from app.utils.prompt_loader import load_system_prompts
from app.services.agent_tools import (
    rag_summarize,
    _get_rag_service,
    build_evidence_cards,
    get_weather,
    get_session_summary,
    trigger_report,
    get_current_month,
    get_user_profile,
    get_confirmed_memories,
    get_fitness_summary,
)
from app.services.middleware import monitor_tool, log_before_model, report_prompt_switch
from app.services.rag_service import RagContext, RagSummarizeService
from app.services.chat_routing_graph import (
    ChatGraphState,
    ChatRuntimeContext,
    JsonValue,
    StructuredOutputIntentClassifier,
    build_chat_routing_graph,
    build_initial_chat_state,
    is_json_value,
)
from app.core.settings import get_settings
from app.utils.chat_latency import ChatLatencyTracker

TOOL_DISPLAY = {
    "get_user_profile": "获取用户画像",
    "get_confirmed_memories": "读取已确认记忆",
    "rag_summarize": "检索知识库",
    "get_weather": "查询天气",
    "get_current_month": "获取月份",
    "get_session_summary": "读取早期会话摘要",
    "trigger_report": "生成报告",
    "get_fitness_summary": "获取运动数据",
}


class PersonalizedAgentState(AgentState, total=False):
    """声明内层 Agent 在单次个性化执行中可读写的短期字段。"""

    retrieval_history: list[dict[str, object]]
    rag_evidence: Annotated[list[dict[str, object]], add]
    report: Annotated[bool, or_]


class DirectRagExecutor:
    """封装无 HTTP 依赖的直接检索与模型事件生成流程。"""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        rag_service_factory: Callable[[], RagSummarizeService] | None = None,
        evidence_builder: Callable[
            [object], list[dict[str, str | int | float | None]]
        ] = build_evidence_cards,
    ) -> None:
        """注入模型、检索服务工厂和证据卡片转换器。"""
        self._model = model
        self._rag_service_factory = rag_service_factory or _get_rag_service
        self._evidence_builder = evidence_builder
        self._rag_context_runnable = RunnableLambda(self._build_rag_context).with_config(
            run_name="rag_summarize", tags=["agent_tool"]
        )

    def _build_rag_context(self, payload: dict[str, object]) -> RagContext:
        """基于原始查询构建直接检索所需的证据上下文。"""
        timing = payload.get("timing")
        service = self._rag_service_factory()
        if isinstance(timing, ChatLatencyTracker):
            return service.build_context(str(payload["query"]), timing=timing)
        return service.build_context(str(payload["query"]))

    @staticmethod
    def _content_to_text(content: object) -> str:
        """将模型分块内容统一转换为文本。"""
        if isinstance(content, list):
            return "".join(
                item.get("text", "") if isinstance(item, dict) else str(item) for item in content
            )
        return str(content or "")

    async def astream(
        self,
        *,
        query: str,
        history: list[dict],
        config: RunnableConfig | None = None,
        timing: ChatLatencyTracker | None = None,
    ) -> AsyncIterator[dict]:
        """异步执行直接检索并让调用配置贯穿检索与模型流。"""
        if timing is not None:
            timing.mark("direct_rag.started")
        yield {"type": "tool", "name": TOOL_DISPLAY["rag_summarize"]}
        payload: dict[str, object] = {"query": query, "history": history}
        if timing is not None:
            payload["timing"] = timing
            with timing.span("direct_rag.rag_context"):
                rag_context = cast(
                    RagContext,
                    await asyncio.to_thread(
                        self._rag_context_runnable.invoke, payload, config=config
                    ),
                )
        else:
            rag_context = cast(
                RagContext,
                await asyncio.to_thread(self._rag_context_runnable.invoke, payload, config=config),
            )
        cards = self._evidence_builder(rag_context.result)
        if cards:
            if timing is not None:
                timing.mark("direct_rag.evidence_ready", evidence_count=len(cards))
            yield {"type": "evidence", "items": cards}

        direct_prompt = (
            "下面的知识库证据已经完成检索。请只依据这些证据回答用户，"
            "不要调用工具、不要提及检索过程；采用证据时保留对应 [证据:N] 标记。\n\n"
            f"用户问题：{query}\n\n知识库证据：\n{rag_context.content}"
        )
        if timing is not None:
            timing.mark("direct_rag.model_stream_started")
        try:
            async for chunk in self._model.astream(
                [("system", load_system_prompts()), ("human", direct_prompt)], config=config
            ):
                content = self._content_to_text(getattr(chunk, "content", chunk))
                if content:
                    if timing is not None:
                        timing.mark_once(
                            "model_first_text",
                            "model.first_text",
                            branch="direct_rag",
                            content_chars=len(content),
                        )
                    yield {"type": "text", "content": content}
        except Exception as error:
            if timing is not None:
                timing.mark("direct_rag.model_stream_error", error_type=type(error).__name__)
            raise
        else:
            if timing is not None:
                timing.mark("direct_rag.model_stream_completed")


class ReactAgent:
    """通过意图路由图协调直接检索与个性化工具编排。"""

    def __init__(self):
        """初始化模型、工具编排图和配置的执行步数限制。"""
        settings = get_settings()
        self.model = get_chat_model()
        self.direct_rag_executor = DirectRagExecutor(model=self.model)
        self.max_steps = settings.agent_max_steps
        self.max_tool_calls = settings.agent_max_tool_calls
        self.agent: Any = create_agent(
            model=self.model,
            system_prompt=load_system_prompts(),
            tools=[
                rag_summarize,
                get_weather,
                get_session_summary,
                get_current_month,
                get_user_profile,
                get_confirmed_memories,
                get_fitness_summary,
                trigger_report,
            ],
            middleware=cast(
                Any,
                [
                    ToolCallLimitMiddleware(
                        run_limit=self.max_tool_calls,
                        exit_behavior="continue",
                    ),
                    monitor_tool,
                    log_before_model,
                    report_prompt_switch,
                ],
            ),
            state_schema=PersonalizedAgentState,
            context_schema=ChatRuntimeContext,
        )
        self.routing_graph: Any = build_chat_routing_graph(
            classifier=StructuredOutputIntentClassifier(self.model)
        )

    async def astream_personalized_events(
        self,
        state: ChatGraphState,
        context: ChatRuntimeContext,
        stream_writer: Callable[[dict], None] | None = None,
        config: RunnableConfig | None = None,
        timing: ChatLatencyTracker | None = None,
    ) -> dict:
        """转发内层事件，并让外层运行配置贯穿 Agent 调用。"""
        latest_user_index = max(
            index for index, message in enumerate(state["messages"]) if message["role"] == "user"
        )
        retrieval_history = [
            dict(message) for message in state["messages"][:latest_user_index][-6:]
        ]
        input_state = {
            "messages": state["messages"],
            "retrieval_history": retrieval_history,
            "rag_evidence": state["rag_evidence"],
            "report": False,
        }
        events = []
        latest_state = input_state
        seen_tool_ids = set()
        pending_tool_call_ids = set()
        emitted_text_since_last_tool = False
        evidence_pending = False
        emitted_evidence_count = len(input_state["rag_evidence"])

        def emit(event: dict) -> None:
            """仅将可序列化事件转发到外层图和 SSE 适配器。"""
            if not is_json_value(event):
                raise ValueError("个性化 Agent 事件包含不可序列化值")
            if stream_writer:
                stream_writer(event)
            events.append(event)

        if timing is not None:
            timing.mark("agent.personalized_model_stream_started")
        try:
            async for stream_mode, payload in self.agent.astream(
                input_state,
                stream_mode=["messages", "values"],
                context=context,
                config=merge_configs(config or {}, {"recursion_limit": self.max_steps}),
            ):
                if stream_mode == "messages":
                    message, metadata = payload
                    if isinstance(message, (AIMessage, AIMessageChunk)):
                        tool_calls = (
                            getattr(message, "tool_call_chunks", None)
                            or getattr(message, "tool_calls", None)
                            or []
                        )
                        for tool_call in tool_calls:
                            tool_id = tool_call.get("id")
                            tool_name = tool_call.get("name")
                            if tool_id and tool_name and tool_id not in seen_tool_ids:
                                seen_tool_ids.add(tool_id)
                                if not pending_tool_call_ids and emitted_text_since_last_tool:
                                    # 工具调用前的说明不是最终答案，通知客户端撤回该临时文本。
                                    emit({"type": "text_reset"})
                                    emitted_text_since_last_tool = False
                                pending_tool_call_ids.add(tool_id)
                                if timing is not None:
                                    timing.mark("agent.tool_requested", tool=tool_name)
                                event = {
                                    "type": "tool",
                                    "id": tool_id,
                                    "name": TOOL_DISPLAY.get(tool_name, tool_name),
                                }
                                emit(event)
                        if message.content and not pending_tool_call_ids:
                            if timing is not None:
                                timing.mark_once(
                                    "model_first_text",
                                    "model.first_text",
                                    branch="personalized_agent",
                                    content_chars=len(str(message.content)),
                                )
                            event = {"type": "text", "content": message.content}
                            emit(event)
                            emitted_text_since_last_tool = True
                    elif isinstance(message, ToolMessage):
                        pending_tool_call_ids.discard(message.tool_call_id)
                        emit({"type": "tool_completed", "id": message.tool_call_id})
                        evidence_pending = True
                elif stream_mode == "values":
                    latest_state = payload
                    evidence = latest_state.get("rag_evidence", [])
                    new_evidence = evidence[emitted_evidence_count:]
                    if evidence_pending and new_evidence:
                        event = {"type": "evidence", "items": new_evidence}
                        emit(event)
                    emitted_evidence_count = len(evidence)
                    evidence_pending = False
        except Exception as error:
            if timing is not None:
                timing.mark(
                    "agent.personalized_model_stream_error", error_type=type(error).__name__
                )
            raise
        else:
            if timing is not None:
                timing.mark("agent.personalized_model_stream_completed")
        output = {
            "retrieval_history": latest_state.get("retrieval_history", state["retrieval_history"]),
            "rag_evidence": latest_state.get("rag_evidence", state["rag_evidence"]),
            "events": events,
        }
        if not is_json_value(output):
            raise ValueError("个性化 Agent 产物包含不可序列化值")
        return output

    @staticmethod
    def _normalize_messages(messages: Iterable[dict]) -> list[dict]:
        """过滤无效角色或空内容，规范化可传给 Agent 的消息。"""
        normalized = []
        for message in messages:
            role = message.get("role")
            content = (message.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    async def execute_stream(
        self,
        messages: list[dict],
        user_id: int | None = None,
        session_id: str = "",
        config: RunnableConfig | None = None,
        timing: ChatLatencyTracker | None = None,
    ) -> AsyncIterator[dict[str, JsonValue]]:
        """构造请求级图上下文，并产出已验证的内部事件。"""
        if timing is not None:
            timing.mark("agent.execute_stream_started", message_count=len(messages))
        normalized_messages = self._normalize_messages(messages)
        initial_state = build_initial_chat_state(normalized_messages)
        runtime_context = ChatRuntimeContext(
            user_id=user_id or 0,
            session_id=session_id,
            dependencies=SimpleNamespace(
                direct_rag_executor=self.direct_rag_executor,
                personalized_agent_executor=self,
                max_tool_calls=self.max_tool_calls,
                latency_tracker=timing,
            ),
        )
        async for stream_mode, event in self.routing_graph.astream(
            initial_state,
            context=runtime_context,
            stream_mode=["custom", "values"],
            config=config,
        ):
            if stream_mode == "custom":
                if not isinstance(event, dict) or not is_json_value(event):
                    raise ValueError("Agent 输出了不可序列化的内部事件")
                yield event


if __name__ == "__main__":

    async def _main() -> None:
        """以异步方式演示聊天事件流。"""

        agent = ReactAgent()
        async for chunk in agent.execute_stream(
            [{"role": "user", "content": "我想减脂，应该怎么练？"}]
        ):
            print(chunk, end="", flush=True)

    asyncio.run(_main())
