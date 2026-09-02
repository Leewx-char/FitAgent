"""LangGraph 聊天路由的状态与意图分类契约。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Literal, Protocol, TypeAlias, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from app.services.session_facts import extract_session_facts


Route = Literal["direct_rag", "personalized_agent"]
JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
ChatGraphNode: TypeAlias = Callable[
    ["ChatGraphState", Runtime["ChatRuntimeContext"]], dict[str, JsonValue]
]


class IntentDecision(BaseModel):
    """约束分类模型只能返回两个受支持的路由。"""

    route: Route


class ChatMessage(TypedDict):
    """表示聊天图状态中可序列化的一条标准消息。"""

    role: str
    content: str


class ChatGraphState(TypedDict):
    """描述一次聊天图执行中可变的短生命周期状态。"""

    messages: list[ChatMessage]
    session_facts: dict[str, JsonValue]
    session_summary: str
    retrieval_history: list[dict[str, JsonValue]]
    route: Route | None
    rag_evidence: list[dict[str, JsonValue]]
    tool_call_count: int
    events: list[dict[str, JsonValue]]


@dataclass(frozen=True)
class ChatRuntimeContext:
    """保存单次请求注入的身份、追踪与执行依赖。"""

    user_id: int
    city: str
    session_id: str
    trace: object
    dependencies: object


class IntentClassifier(Protocol):
    """定义意图分类器在路由节点使用的最小接口。"""

    def classify(self, prompt: str) -> IntentDecision:
        """根据受限提示词返回经过结构化约束的路由。"""
        ...


class StructuredOutputIntentClassifier:
    """将支持结构化输出的聊天模型适配为意图分类器。"""

    def __init__(self, model: object) -> None:
        """保存延迟包装为结构化输出模型的聊天模型实例。"""
        self._model = model

    def classify(self, prompt: str) -> IntentDecision:
        """调用模型结构化输出，并校验为固定路由枚举。"""
        structured_model = self._model.with_structured_output(IntentDecision)
        return IntentDecision.model_validate(structured_model.invoke(prompt))


def classify_intent(state: ChatGraphState, classifier: IntentClassifier) -> Route:
    """从最后一条用户消息分类，异常时保守回退个性化分支。"""
    try:
        if not _is_json_value(state):
            raise ValueError("图状态包含不可序列化值")
        message = _latest_user_message(state["messages"])
        facts = _minimal_session_facts(state["session_facts"])
        prompt = _build_classifier_prompt(message, facts)
        return IntentDecision.model_validate(classifier.classify(prompt)).route
    except Exception:
        return "personalized_agent"


def _is_json_value(value: object) -> bool:
    """递归判断值能否作为图状态中的 JSON 数据保存。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _latest_user_message(messages: object) -> str:
    """提取并校验消息列表中最后一条非空用户消息。"""
    if not isinstance(messages, list):
        raise ValueError("messages 必须是列表")
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    raise ValueError("缺少用户消息")


def _minimal_session_facts(session_facts: object) -> str:
    """将有限会话事实压缩为分类提示词可用的文本。"""
    if not isinstance(session_facts, dict):
        raise ValueError("session_facts 必须是字典")
    return "\n".join(
        f"{key}: {str(value)[:120]}"
        for key, value in list(session_facts.items())[:5]
        if isinstance(key, str) and value is not None
    ) or "无"


def _build_classifier_prompt(message: str, session_facts: str) -> str:
    """构造只含当前问题和最小会话事实的分类提示词。"""
    return f"""你是健身对话路由分类器，只返回结构化 route。
仅当问题是无需用户个人信息的单一通用健身知识问答时选择 direct_rag。
涉及伤病、训练目标、饮食、计划、历史记录、个人资料，或意图不确定时，选择 personalized_agent。

最小会话事实：
{session_facts}

最后一条用户消息：
{message}"""


def build_initial_chat_state(
    messages: Iterable[Mapping[str, object]], session_summary: str
) -> ChatGraphState:
    """标准化消息并初始化一次图执行所需的短期状态。"""
    normalized_messages = [
        {"role": str(message["role"]), "content": str(message["content"]).strip()}
        for message in messages
    ]
    return {
        "messages": normalized_messages,
        "session_facts": extract_session_facts(normalized_messages),
        "session_summary": session_summary,
        "retrieval_history": [],
        "route": None,
        "rag_evidence": [],
        "tool_call_count": 0,
        "events": [],
    }


def route_after_classification(state: ChatGraphState) -> Route:
    """只让明确的直接检索结果通过，其余结果保守地进入个性化分支。"""
    if state.get("route") == "direct_rag":
        return "direct_rag"
    return "personalized_agent"


def _classify_intent_node(
    state: ChatGraphState,
    runtime: Runtime[ChatRuntimeContext],
    *,
    classifier: IntentClassifier,
) -> dict[str, Route]:
    """调用分类契约并仅将路由结果写回图状态。"""
    del runtime
    return {"route": classify_intent(state, classifier)}


def _empty_execution_node(
    _state: ChatGraphState, runtime: Runtime[ChatRuntimeContext]
) -> dict[str, JsonValue]:
    """为后续真实执行器保留不产生状态更新的可注入桩。"""
    del runtime
    return {}


def build_chat_routing_graph(
    *,
    classifier: IntentClassifier,
    direct_rag_node: ChatGraphNode | None = None,
    personalized_agent_node: ChatGraphNode | None = None,
) -> CompiledStateGraph:
    """编译分类后按条件边进入两个可替换执行节点的聊天图。"""
    graph = StateGraph(ChatGraphState, context_schema=ChatRuntimeContext)
    graph.add_node(
        "classify_intent",
        partial(_classify_intent_node, classifier=classifier),
    )
    graph.add_node("direct_rag", direct_rag_node or _empty_execution_node)
    graph.add_node("personalized_agent", personalized_agent_node or _empty_execution_node)
    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_after_classification,
        {
            "direct_rag": "direct_rag",
            "personalized_agent": "personalized_agent",
        },
    )
    graph.add_edge("direct_rag", END)
    graph.add_edge("personalized_agent", END)
    return graph.compile()
