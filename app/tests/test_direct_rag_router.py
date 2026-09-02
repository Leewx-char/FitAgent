"""明确知识问答的 RAG 快速路径判定测试。"""

from types import SimpleNamespace

import pytest

from app.services import react_agent
from app.services.agent_trace import AgentTrace
from app.services.chat_routing_graph import (
    ChatRuntimeContext,
    IntentDecision,
    StructuredOutputIntentClassifier,
    build_chat_routing_graph,
    build_initial_chat_state,
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


def test_direct_rag_graph_emits_tool_evidence_then_text():
    """验证图分支按工具、证据、文本顺序保存兼容事件。"""
    captured = {}

    class FakeRagService:
        """记录请求并返回固定检索上下文。"""

        @staticmethod
        def build_context(query, history):
            """捕获直接 RAG 的查询与历史，并返回固定检索结果。"""
            captured["query"] = query
            captured["history"] = history
            return SimpleNamespace(content="[证据:1] 深蹲资料", result="retrieval-result")

    class FakeModel:
        """提供无需真实模型的固定文本流。"""

        @staticmethod
        def stream(_messages):
            """返回一条带证据标记的固定模型输出。"""
            return [SimpleNamespace(content="膝盖跟随脚尖。[证据:1]")]

    executor = react_agent.DirectRagExecutor(
        model=FakeModel(),
        rag_service_factory=FakeRagService,
        evidence_builder=lambda _result: [{"rank": 1, "evidence_id": "guide.md#1"}],
    )
    graph = build_chat_routing_graph(
        classifier=FakeIntentClassifier(IntentDecision(route="direct_rag"))
    )
    result = graph.invoke(
        build_initial_chat_state(
            messages=[
                {"role": "user", "content": "先说深蹲。"},
                {"role": "assistant", "content": "好的。"},
                {"role": "user", "content": "那膝盖呢？"},
            ],
            session_summary="",
        ),
        context=ChatRuntimeContext(
            user_id=1,
            city="",
            session_id="session-1",
            trace=None,
            dependencies=SimpleNamespace(direct_rag_executor=executor),
        ),
    )

    assert [event["type"] for event in result["events"]] == ["tool", "evidence", "text"]
    assert result["events"][1]["items"][0]["evidence_id"] == "guide.md#1"
    assert result["rag_evidence"] == [{"rank": 1, "evidence_id": "guide.md#1"}]
    assert captured["query"] == "那膝盖呢？"
    assert captured["history"] == [
        {"role": "user", "content": "先说深蹲。"},
        {"role": "assistant", "content": "好的。"},
    ]


def test_direct_rag_graph_marks_trace_mode_direct_rag():
    """验证图分支将请求轨迹标记为直接检索模式。"""

    class FakeRagService:
        """提供无需外部服务的固定检索上下文。"""

        @staticmethod
        def build_context(_query, history):
            """返回无证据的固定上下文。"""
            return SimpleNamespace(content="未检索到资料", result=None)

    class FakeModel:
        """提供无需真实模型的固定文本流。"""

        @staticmethod
        def stream(_messages):
            """返回固定回答分块。"""
            return [SimpleNamespace(content="暂时没有可靠证据。")]

    trace = AgentTrace(request_id="request-1")
    executor = react_agent.DirectRagExecutor(
        model=FakeModel(),
        rag_service_factory=FakeRagService,
    )
    graph = build_chat_routing_graph(
        classifier=FakeIntentClassifier(IntentDecision(route="direct_rag"))
    )

    graph.invoke(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "解释一下深蹲。"}],
            session_summary="",
        ),
        context=ChatRuntimeContext(
            user_id=1,
            city="",
            session_id="session-1",
            trace=trace,
            dependencies=SimpleNamespace(direct_rag_executor=executor),
        ),
    )

    assert trace.mode == "direct_rag"


def test_direct_rag_graph_rejects_non_json_executor_events():
    """验证执行器的非 JSON 事件无法写入图状态。"""

    class FakeDirectRagExecutor:
        """生成包含运行时对象的非法事件。"""

        @staticmethod
        def stream(**_kwargs):
            """返回无法序列化的证据事件。"""
            yield {"type": "evidence", "items": [{"unsafe": object()}]}

    graph = build_chat_routing_graph(
        classifier=FakeIntentClassifier(IntentDecision(route="direct_rag"))
    )

    with pytest.raises(ValueError, match="不可序列化"):
        graph.invoke(
            build_initial_chat_state(
                messages=[{"role": "user", "content": "解释一下深蹲。"}],
                session_summary="",
            ),
            context=ChatRuntimeContext(
                user_id=1,
                city="",
                session_id="session-1",
                trace=None,
                dependencies=SimpleNamespace(direct_rag_executor=FakeDirectRagExecutor()),
            ),
        )
