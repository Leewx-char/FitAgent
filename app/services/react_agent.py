import json
import time
from typing import Callable, Iterable, Iterator
from langchain.agents import create_agent
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
from app.services.session_facts import extract_session_facts
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
    """面向多工具场景的 Agent，并为明确知识问答提供单次模型的 RAG 快速路径。"""

    _KNOWLEDGE_TERMS = (
        "深蹲",
        "硬拉",
        "卧推",
        "引体",
        "跑步",
        "动作",
        "训练",
        "热身",
        "拉伸",
        "增肌",
        "减脂",
        "蛋白",
        "营养",
        "饮食",
        "膝",
        "肩",
        "腰",
        "疼痛",
        "受伤",
        "恢复",
    )
    _PERSONALIZATION_TERMS = (
        "我的",
        "我自己",
        "结合我",
        "根据我",
        "给我制定",
        "我的画像",
        "我的体重",
        "我的身高",
        "我的目标",
        "我的伤",
        "训练记录",
        "运动数据",
        "体检",
        "报告",
        "天气",
        "户外",
    )

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
        )

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

    @classmethod
    def _should_use_direct_rag(cls, messages: list[dict]) -> bool:
        """仅让非个性化明确知识问题走直接检索，避免遗漏必需的个人信息。"""
        if not messages or messages[-1].get("role") != "user":
            return False
        query = str(messages[-1].get("content", "")).strip()
        return (
            bool(query)
            and not any(term in query for term in cls._PERSONALIZATION_TERMS)
            and any(term in query for term in cls._KNOWLEDGE_TERMS)
        )

    def _execute_direct_rag(self, messages: list[dict], trace: AgentTrace | None = None):
        """将直接检索执行器的事件逐条编码为既有 JSON 行。"""
        query = str(messages[-1]["content"])
        history = messages[:-1][-6:]
        executor = getattr(self, "direct_rag_executor", None)
        if executor is None:
            executor = DirectRagExecutor(model=self.model)
        for event in executor.stream(query=query, history=history, trace=trace):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    def execute_stream(
        self,
        messages: list[dict],
        user_id: int | None = None,
        city: str = "",
        session_summary: str = "",
        trace: AgentTrace | None = None,
    ):
        """流式执行直接检索或完整 Agent，并产出工具、证据和文本事件。"""
        normalized_messages = self._normalize_messages(messages)
        session_facts = extract_session_facts(normalized_messages)
        input_dict = {"messages": normalized_messages}

        run_context = {
            "report": False,
            "session_facts": session_facts,
            "tool_call_limit": self.max_tool_calls,
            "tool_call_count": 0,
            "agent_trace": trace,
            "session_summary": session_summary,
        }
        # 当前消息由工具参数携带；检索器只需要最近历史来消解“那个动作”等指代。
        run_context["retrieval_history"] = normalized_messages[:-1][-6:]
        if user_id:
            run_context["user_id"] = user_id
        if city:
            run_context["city"] = city

        if self._should_use_direct_rag(normalized_messages):
            yield from self._execute_direct_rag(normalized_messages, trace)
            return

        seen_tool_ids = set()  # 记录已见过的工具调用ID，用于去重和判断"是否调过工具"
        last_tool_step = None  # 记录最后一个 ToolMessage 所在的 step 编号
        # None 表示：还没执行完所有工具调用（还在调工具阶段）

        for msg_chunk, metadata in self.agent.stream(
            input_dict,
            stream_mode="messages",
            context=run_context,
            config={"recursion_limit": self.max_steps},
        ):
            if isinstance(msg_chunk, AIMessageChunk):
                tool_call_chunks = getattr(msg_chunk, "tool_call_chunks", None) or []
                # 工具调用通知
                for tc_chunk in tool_call_chunks:
                    if tc_chunk.get("name") and tc_chunk.get("id"):
                        if tc_chunk["id"] not in seen_tool_ids:
                            seen_tool_ids.add(tc_chunk["id"])
                            last_tool_step = None  # ← 关键：见到新工具调用，重置为 None
                            yield (
                                json.dumps(
                                    {
                                        "type": "tool",
                                        "name": TOOL_DISPLAY.get(
                                            tc_chunk["name"], tc_chunk["name"]
                                        ),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )  # 前端显示"🔍 获取画像..."
                # 文本内容
                if msg_chunk.content:
                    if not seen_tool_ids or (
                        last_tool_step is not None
                        and metadata.get("langgraph_step", 0) > last_tool_step
                    ):
                        yield (
                            json.dumps(
                                {"type": "text", "content": msg_chunk.content}, ensure_ascii=False
                            )
                            + "\n"
                        )
            elif isinstance(msg_chunk, ToolMessage):
                last_tool_step = metadata.get("langgraph_step", 0)
                evidence = run_context.get("rag_evidence", [])
                if evidence and not run_context.get("rag_evidence_emitted", False):
                    run_context["rag_evidence_emitted"] = True
                    yield (
                        json.dumps({"type": "evidence", "items": evidence}, ensure_ascii=False)
                        + "\n"
                    )


if __name__ == "__main__":
    agent = ReactAgent()
    res = agent.execute_stream([{"role": "user", "content": "我想减脂，应该怎么练？"}])
    for chunk in res:
        print(chunk, end="", flush=True)
