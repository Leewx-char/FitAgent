"""LangGraph 聊天路由的状态与意图分类契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from pydantic import BaseModel


Route = Literal["direct_rag", "personalized_agent"]


class IntentDecision(BaseModel):
    """约束分类模型只能返回两个受支持的路由。"""

    route: Route


class ChatGraphState(TypedDict):
    """描述一次聊天图执行中可变的短生命周期状态。"""

    messages: list[dict[str, object]]
    session_facts: dict[str, object]
    session_summary: str
    retrieval_history: list[dict[str, object]]
    route: Route | None
    rag_evidence: list[object]
    tool_call_count: int
    events: list[dict[str, object]]


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
        message = _latest_user_message(state["messages"])
        facts = _minimal_session_facts(state["session_facts"])
        prompt = _build_classifier_prompt(message, facts)
        return IntentDecision.model_validate(classifier.classify(prompt)).route
    except Exception:
        return "personalized_agent"


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
