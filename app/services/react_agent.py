import json
import time
from types import SimpleNamespace
from typing import Callable, Iterable, Iterator
from langchain.agents import AgentState, create_agent
from langchain_core.messages import AIMessageChunk, ToolMessage

from app.services.factory import get_chat_model
from app.utils.prompt_loader import load_system_prompts
from app.services.agent_tools import (
    rag_summarize,
    _get_rag_service,
    build_evidence_cards,
    get_weather,
    get_user_location,
    get_user_id,
    trigger_report,
    get_current_month,
    get_user_profile,
    get_confirmed_memories,
    get_fitness_summary,
)
from app.services.middleware import monitor_tool, log_before_model, report_prompt_switch
from app.services.agent_trace import AgentTrace
from app.services.chat_routing_graph import (
    ChatGraphState,
    ChatRuntimeContext,
    StructuredOutputIntentClassifier,
    build_chat_routing_graph,
    build_initial_chat_state,
)
from app.core.settings import get_settings

TOOL_DISPLAY = {
    "get_user_profile": "获取用户画像",
    "get_confirmed_memories": "读取已确认记忆",
    "rag_summarize": "检索知识库",
    "get_weather": "查询天气",
    "get_user_location": "获取位置",
    "get_current_month": "获取月份",
    "get_user_id": "获取用户ID",
    "trigger_report": "生成报告",
    "get_fitness_summary": "获取运动数据",
}


class PersonalizedAgentState(AgentState, total=False):
    """声明内层 Agent 在单次个性化执行中可读写的短期字段。"""

    session_facts: dict[str, object]
    session_summary: str
    retrieval_history: list[dict[str, object]]
    rag_evidence: list[dict[str, object]]
    tool_call_limit: int
    tool_call_count: int
    report: bool


class DirectRagExecutor:
    """封装无 HTTP 依赖的直接检索与模型事件生成流程。"""

    def __init__(
        self,
        *,
        model: object,
        rag_service_factory: Callable[[], object] | None = None,
        evidence_builder: Callable[[object], list[dict]] = build_evidence_cards,
    ) -> None:
        """注入模型、检索服务工厂和证据卡片转换器。"""
        self._model = model
        self._rag_service_factory = rag_service_factory or _get_rag_service
        self._evidence_builder = evidence_builder

    @staticmethod
    def _content_to_text(content: object) -> str:
        """将模型分块内容统一转换为文本。"""
        if isinstance(content, list):
            return "".join(
                item.get("text", "") if isinstance(item, dict) else str(item) for item in content
            )
        return str(content or "")

    def stream(
        self,
        *,
        query: str,
        history: list[dict],
        trace: AgentTrace | None = None,
    ) -> Iterator[dict]:
        """按工具、证据、文本顺序生成兼容既有 SSE 的事件对象。"""
        if trace:
            trace.mode = "direct_rag"
        yield {"type": "tool", "name": TOOL_DISPLAY["rag_summarize"]}
        started_at = time.perf_counter()
        try:
            rag_context = self._rag_service_factory().build_context(query, history=history)
        except Exception:
            if trace:
                trace.record_tool(
                    tool_name="rag_summarize",
                    argument_shape={"query": "str", "source": "str"},
                    status="error",
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000),
                    detail="internal_error",
                )
            raise
        if trace:
            trace.record_tool(
                tool_name="rag_summarize",
                argument_shape={"query": "str", "source": "str"},
                status="success",
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            )
        cards = self._evidence_builder(rag_context.result)
        if cards:
            yield {"type": "evidence", "items": cards}

        direct_prompt = (
            "下面的知识库证据已经完成检索。请只依据这些证据回答用户，"
            "不要调用工具、不要提及检索过程；采用证据时保留对应 [证据:N] 标记。\n\n"
            f"用户问题：{query}\n\n知识库证据：\n{rag_context.content}"
        )
        for chunk in self._model.stream(
            [("system", load_system_prompts()), ("human", direct_prompt)]
        ):
            content = self._content_to_text(getattr(chunk, "content", chunk))
            if content:
                yield {"type": "text", "content": content}


class ReactAgent:
    """通过意图路由图协调直接检索与个性化工具编排。"""

    def __init__(self):
        """初始化模型、工具编排图和配置的执行步数限制。"""
        settings = get_settings()
        self.model = get_chat_model()
        self.direct_rag_executor = DirectRagExecutor(model=self.model)
        self.max_steps = settings.agent_max_steps
        self.max_tool_calls = settings.agent_max_tool_calls
        self.agent = create_agent(
            model=self.model,
            system_prompt=load_system_prompts(),
            tools=[
                rag_summarize,
                get_weather,
                get_user_location,
                get_user_id,
                get_current_month,
                get_user_profile,
                get_confirmed_memories,
                get_fitness_summary,
                trigger_report,
            ],
            middleware=[monitor_tool, log_before_model, report_prompt_switch],
            state_schema=PersonalizedAgentState,
            context_schema=ChatRuntimeContext,
        )
        self.routing_graph = build_chat_routing_graph(
            classifier=StructuredOutputIntentClassifier(self.model)
        )

    def stream_personalized_events(
        self,
        state: ChatGraphState,
        context: ChatRuntimeContext,
        stream_writer: Callable[[dict], None] | None = None,
    ) -> dict:
        """通过可选写入器实时输出内层事件，并保留图状态所需产物。"""
        if context.trace is not None:
            context.trace.mode = "agent"
        latest_user_index = max(
            index for index, message in enumerate(state["messages"]) if message["role"] == "user"
        )
        retrieval_history = [
            dict(message) for message in state["messages"][:latest_user_index][-6:]
        ]
        input_state = {
            "messages": state["messages"],
            "session_facts": state["session_facts"],
            "session_summary": state["session_summary"],
            "retrieval_history": retrieval_history,
            "rag_evidence": state["rag_evidence"],
            "tool_call_limit": self.max_tool_calls,
            "tool_call_count": state["tool_call_count"],
            "report": False,
        }
        events = []
        latest_state = input_state
        seen_tool_ids = set()
        last_tool_step = None
        evidence_pending = False
        emitted_evidence_count = len(input_state["rag_evidence"])
        for stream_mode, payload in self.agent.stream(
            input_state,
            stream_mode=["messages", "values"],
            context=context,
            config={"recursion_limit": self.max_steps},
        ):
            if stream_mode == "messages":
                message, metadata = payload
                if isinstance(message, AIMessageChunk):
                    for tool_call in getattr(message, "tool_call_chunks", None) or []:
                        tool_id = tool_call.get("id")
                        tool_name = tool_call.get("name")
                        if tool_id and tool_name and tool_id not in seen_tool_ids:
                            seen_tool_ids.add(tool_id)
                            last_tool_step = None
                            event = {
                                "type": "tool",
                                "name": TOOL_DISPLAY.get(tool_name, tool_name),
                            }
                            if stream_writer:
                                stream_writer(event)
                            events.append(event)
                    if message.content and (
                        not seen_tool_ids
                        or (
                            last_tool_step is not None
                            and metadata.get("langgraph_step", 0) > last_tool_step
                        )
                    ):
                        event = {"type": "text", "content": message.content}
                        if stream_writer:
                            stream_writer(event)
                        events.append(event)
                elif isinstance(message, ToolMessage):
                    last_tool_step = metadata.get("langgraph_step", 0)
                    evidence_pending = True
            elif stream_mode == "values":
                latest_state = payload
                evidence = latest_state.get("rag_evidence", [])
                new_evidence = evidence[emitted_evidence_count:]
                if evidence_pending and new_evidence:
                    event = {"type": "evidence", "items": new_evidence}
                    if stream_writer:
                        stream_writer(event)
                    events.append(event)
                emitted_evidence_count = len(evidence)
                evidence_pending = False
        return {
            "retrieval_history": latest_state.get("retrieval_history", state["retrieval_history"]),
            "rag_evidence": latest_state.get("rag_evidence", state["rag_evidence"]),
            "tool_call_count": latest_state.get("tool_call_count", state["tool_call_count"]),
            "events": events,
        }

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

    def execute_stream(
        self,
        messages: list[dict],
        user_id: int | None = None,
        city: str = "",
        session_id: str = "",
        session_summary: str = "",
        trace: AgentTrace | None = None,
    ):
        """构造请求级图上下文，并编码兼容既有 SSE 的执行事件。"""
        normalized_messages = self._normalize_messages(messages)
        initial_state = build_initial_chat_state(normalized_messages, session_summary)
        runtime_context = ChatRuntimeContext(
            user_id=user_id or 0,
            city=city or str(initial_state["session_facts"].get("city", "")),
            session_id=session_id,
            trace=trace,
            dependencies=SimpleNamespace(
                direct_rag_executor=self.direct_rag_executor,
                personalized_agent_executor=self,
                max_tool_calls=self.max_tool_calls,
            ),
        )
        for stream_mode, event in self.routing_graph.stream(
            initial_state,
            context=runtime_context,
            stream_mode=["custom", "values"],
        ):
            if stream_mode == "custom":
                yield json.dumps(event, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    agent = ReactAgent()
    res = agent.execute_stream([{"role": "user", "content": "我想减脂，应该怎么练？"}])
    for chunk in res:
        print(chunk, end="", flush=True)
