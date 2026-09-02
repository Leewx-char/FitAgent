"""明确知识问答的 RAG 快速路径判定测试。"""

import json
from types import SimpleNamespace

from app.services import react_agent
from app.services.chat_routing_graph import (
    IntentDecision,
    StructuredOutputIntentClassifier,
    classify_intent,
)
from app.services.react_agent import ReactAgent


class FakeIntentClassifier:
    def __init__(self, result):
        self.result = result

    def classify(self, _prompt):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_structured_output_classifier_adapts_model_result():
    captured = {}

    class FakeStructuredModel:
        @staticmethod
        def invoke(prompt):
            captured["prompt"] = prompt
            return {"route": "direct_rag"}

    class FakeModel:
        @staticmethod
        def with_structured_output(schema):
            captured["schema"] = schema
            return FakeStructuredModel()

    decision = StructuredOutputIntentClassifier(FakeModel()).classify("分类问题")

    assert decision.route == "direct_rag"
    assert captured == {"schema": IntentDecision, "prompt": "分类问题"}


def test_classifier_routes_generic_knowledge_question_to_direct_rag():
    route = classify_intent(
        {
            "messages": [{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
            "session_facts": {},
        },
        FakeIntentClassifier(IntentDecision(route="direct_rag")),
    )

    assert route == "direct_rag"


def test_classifier_routes_personalized_question_to_agent():
    route = classify_intent(
        {
            "messages": [{"role": "user", "content": "结合我的体重安排减脂训练。"}],
            "session_facts": {"weight": "75kg", "goal": "减脂"},
        },
        FakeIntentClassifier(IntentDecision(route="personalized_agent")),
    )

    assert route == "personalized_agent"


def test_classifier_failure_falls_back_to_personalized_agent():
    route = classify_intent(
        {
            "messages": [{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
            "session_facts": {},
        },
        FakeIntentClassifier(RuntimeError("classifier unavailable")),
    )

    assert route == "personalized_agent"


def test_invalid_structured_result_falls_back_to_personalized_agent():
    route = classify_intent(
        {
            "messages": [{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
            "session_facts": {},
        },
        FakeIntentClassifier({"route": "unsupported"}),
    )

    assert route == "personalized_agent"


def test_classifier_rejects_runtime_object_from_graph_state():
    route = classify_intent(
        {
            "messages": [{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
            "session_facts": {},
            "events": [{"trace": object()}],
        },
        FakeIntentClassifier(IntentDecision(route="direct_rag")),
    )

    assert route == "personalized_agent"


def test_direct_rag_accepts_generic_knowledge_question():
    """验证通用动作知识问题可进入直接 RAG 快速路径。"""
    assert ReactAgent._should_use_direct_rag(
        [{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}]
    )


def test_direct_rag_keeps_personalized_question_in_full_agent_flow():
    """验证包含个人条件的问题仍走完整 Agent 流程。"""
    assert not ReactAgent._should_use_direct_rag(
        [{"role": "user", "content": "结合我的体重和目标，帮我安排减脂训练。"}]
    )


def test_direct_rag_streams_real_evidence_before_answer(monkeypatch):
    """验证直接 RAG 在答案前依次输出工具与真实证据事件。"""
    captured = {}

    class FakeRagService:
        @staticmethod
        def build_context(query, history):
            """捕获直接 RAG 的查询与历史，并返回固定检索结果。"""
            captured["query"] = query
            captured["history"] = history
            return SimpleNamespace(content="[证据:1] 深蹲资料", result="retrieval-result")

    class FakeModel:
        @staticmethod
        def stream(_messages):
            """返回一条带证据标记的固定模型输出。"""
            return [SimpleNamespace(content="膝盖跟随脚尖。[证据:1]")]

    monkeypatch.setattr(react_agent, "_get_rag_service", lambda: FakeRagService())
    monkeypatch.setattr(
        react_agent,
        "build_evidence_cards",
        lambda _result: [{"rank": 1, "evidence_id": "guide.md#1"}],
    )
    agent = object.__new__(ReactAgent)
    agent.model = FakeModel()

    events = [
        json.loads(item)
        for item in agent._execute_direct_rag(
            [
                {"role": "user", "content": "先说深蹲。"},
                {"role": "assistant", "content": "好的。"},
                {"role": "user", "content": "那膝盖呢？"},
            ]
        )
    ]

    assert [event["type"] for event in events] == ["tool", "evidence", "text"]
    assert events[1]["items"][0]["evidence_id"] == "guide.md#1"
    assert captured["history"] == [
        {"role": "user", "content": "先说深蹲。"},
        {"role": "assistant", "content": "好的。"},
    ]
