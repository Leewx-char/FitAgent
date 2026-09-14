import asyncio
import json
from collections.abc import Mapping
from operator import add, or_
from types import SimpleNamespace
from typing import Annotated, Any, AsyncIterator, Callable, Iterable, cast
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
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
from app.core.request_context import request_id_var
from app.utils.chat_latency import ChatLatencyTracker
from app.utils.logger_handler import logger

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


def _event_content_to_text(content: object) -> str:
    """将 LangChain 事件中的模型内容规范成可发送的文本。"""
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item) for item in content
        )
    return str(content or "")


def _log_agent_stream_event(event: Mapping[str, object]) -> None:
    """记录原始事件的脱敏摘要，不写入提示词、回答或工具参数。"""
    event_name = event.get("event")
    if event_name not in {"on_chat_model_end", "on_tool_start", "on_tool_end"}:
        return
    data = event.get("data")
    output = data.get("output") if isinstance(data, Mapping) else None
    content = getattr(output, "content", "")
    logger.info(
        "AGENT_STREAM_EVENT %s",
        json.dumps(
            {
                "request_id": request_id_var.get(),
                "event": event_name,
                "name": event.get("name"),
                "run_id": str(event.get("run_id", "")),
                "content_chars": len(_event_content_to_text(content)),
            },
            ensure_ascii=False,
        ),
    )


def _rag_evidence_from_tool_output(output: object) -> list[dict[str, object]]:
    """从 rag_summarize 的 Command 更新中取回本次新增证据。"""
    update = getattr(output, "update", None)
    if not isinstance(update, Mapping) and isinstance(output, Mapping):
        update = output.get("update")
    evidence = update.get("rag_evidence") if isinstance(update, Mapping) else None
    if not isinstance(evidence, list):
        return []
    return [item for item in evidence if isinstance(item, dict)]


class _PersonalizedEventAdapter:
    """将内层 LangChain v2 原始事件适配为既有聊天 SSE 事件。"""

    def __init__(
        self,
        *,
        initial_evidence: list[dict[str, object]],
        stream_writer: Callable[[dict], None] | None,
        timing: ChatLatencyTracker | None,
    ) -> None:
        """初始化本次请求独占的事件、工具和证据状态。"""
        self.events: list[dict[str, object]] = []
        self.rag_evidence = list(initial_evidence)
        self._stream_writer = stream_writer
        self._timing = timing
        self._model_runs_with_text: set[str] = set()
        self._started_tool_runs: set[str] = set()
        self._pending_tool_runs: set[str] = set()
        self._completed_tool_runs: set[str] = set()
        self._emitted_text_since_last_tool = False

    def consume(self, event: object) -> None:
        """按原始事件类型转发模型文本、工具状态和检索证据。"""
        if not isinstance(event, Mapping):
            return
        _log_agent_stream_event(event)
        event_name = event.get("event")
        if event_name == "on_chat_model_stream":
            self._consume_model_chunk(event)
        elif event_name == "on_chat_model_end":
            self._consume_model_end(event)
        elif event_name == "on_tool_start":
            self._start_tool(event)
        elif event_name == "on_tool_end":
            self._complete_tool(event)
        elif event_name == "on_tool_error":
            self._complete_tool(event, include_rag_evidence=False)

    def _emit(self, event: dict[str, object]) -> None:
        """校验事件可序列化后转发给外层图和 SSE 生成器。"""
        if not is_json_value(event):
            raise ValueError("个性化 Agent 事件包含不可序列化值")
        if self._stream_writer:
            self._stream_writer(event)
        self.events.append(event)

    def _emit_text(self, content: str) -> None:
        """发送正文并记录本次请求的首个可见文本耗时。"""
        if self._timing is not None:
            self._timing.mark_once(
                "model_first_text",
                "model.first_text",
                branch="personalized_agent",
                content_chars=len(content),
            )
        self._emit({"type": "text", "content": content})
        self._emitted_text_since_last_tool = True

    @staticmethod
    def _event_data(event: Mapping[str, object]) -> Mapping[str, object]:
        """返回事件数据字典，屏蔽缺失或异常格式。"""
        data = event.get("data")
        return data if isinstance(data, Mapping) else {}

    def _consume_model_chunk(self, event: Mapping[str, object]) -> None:
        """转发正式模型分块；工具执行期间忽略未完成的中间文本。"""
        if self._pending_tool_runs:
            return
        chunk = self._event_data(event).get("chunk")
        content = _event_content_to_text(getattr(chunk, "content", chunk))
        if not content:
            return
        run_id = str(event.get("run_id", ""))
        self._emit_text(content)
        self._model_runs_with_text.add(run_id)

    def _consume_model_end(self, event: Mapping[str, object]) -> None:
        """在模型未产生有效分块时，使用最终 AIMessage 兜底正文。"""
        if self._pending_tool_runs:
            return
        run_id = str(event.get("run_id", ""))
        if run_id in self._model_runs_with_text:
            return
        message = self._event_data(event).get("output")
        if not isinstance(message, AIMessage) or message.tool_calls:
            return
        content = _event_content_to_text(message.content)
        if content:
            self._emit_text(content)
            self._model_runs_with_text.add(run_id)

    def _start_tool(self, event: Mapping[str, object]) -> None:
        """以工具运行 ID 建立独立前端工具链，兼容并行同名调用。"""
        run_id = str(event.get("run_id", ""))
        tool_name = event.get("name")
        if not run_id or not isinstance(tool_name, str) or run_id in self._started_tool_runs:
            return
        self._started_tool_runs.add(run_id)
        if self._emitted_text_since_last_tool:
            self._emit({"type": "text_reset"})
            self._emitted_text_since_last_tool = False
        self._pending_tool_runs.add(run_id)
        if self._timing is not None:
            self._timing.mark("agent.tool_requested", tool=tool_name)
        self._emit(
            {
                "type": "tool",
                "id": run_id,
                "name": TOOL_DISPLAY.get(tool_name, tool_name),
            }
        )

    def _complete_tool(
        self, event: Mapping[str, object], *, include_rag_evidence: bool = True
    ) -> None:
        """完成工具链；正常结束的 RAG 工具才发送本次证据。"""
        run_id = str(event.get("run_id", ""))
        if run_id not in self._pending_tool_runs or run_id in self._completed_tool_runs:
            return
        self._pending_tool_runs.remove(run_id)
        self._completed_tool_runs.add(run_id)
        self._emit({"type": "tool_completed", "id": run_id})
        if not include_rag_evidence or event.get("name") != "rag_summarize":
            return
        evidence = _rag_evidence_from_tool_output(self._event_data(event).get("output"))
        if evidence:
            self.rag_evidence.extend(evidence)
            self._emit({"type": "evidence", "items": evidence})


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
        """适配内层 v2 原始事件，并让外层运行配置贯穿 Agent 调用。"""
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
        adapter = _PersonalizedEventAdapter(
            initial_evidence=input_state["rag_evidence"],
            stream_writer=stream_writer,
            timing=timing,
        )

        if timing is not None:
            timing.mark("agent.personalized_model_stream_started")
        stream_outcome = "succeeded"
        stream_error_type: str | None = None
        try:
            async for event in self.agent.astream_events(
                input_state,
                version="v2",
                context=context,
                config=merge_configs(config or {}, {"recursion_limit": self.max_steps}),
            ):
                adapter.consume(event)
        except Exception as error:
            stream_outcome = "failed"
            stream_error_type = type(error).__name__
            if timing is not None:
                timing.mark("agent.personalized_model_stream_error", error_type=stream_error_type)
            raise
        else:
            if timing is not None:
                timing.mark("agent.personalized_model_stream_completed")
        finally:
            text_events = [event for event in adapter.events if event.get("type") == "text"]
            logger.info(
                "AGENT_STREAM_SUMMARY %s",
                json.dumps(
                    {
                        "request_id": request_id_var.get(),
                        "outcome": stream_outcome,
                        "error_type": stream_error_type,
                        "event_count": len(adapter.events),
                        "text_event_count": len(text_events),
                        "text_chars": sum(
                            len(str(event.get("content", ""))) for event in text_events
                        ),
                    },
                    ensure_ascii=False,
                ),
            )
        output = {
            "retrieval_history": retrieval_history,
            "rag_evidence": adapter.rag_evidence,
            "events": adapter.events,
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
