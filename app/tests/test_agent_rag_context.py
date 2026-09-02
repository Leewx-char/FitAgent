"""Agent 知识库工具的会话历史传递测试。"""

import json
from types import SimpleNamespace

from app.services import agent_tools, react_agent
from app.services.chat_routing_graph import (
    ChatRuntimeContext,
    IntentDecision,
    build_chat_routing_graph,
    build_initial_chat_state,
)


class FakeIntentClassifier:
    """固定选择直接检索分支。"""

    def classify(self, _prompt):
        """返回受约束的直接检索决策。"""
        return IntentDecision(route="direct_rag")


def test_rag_tool_forwards_recent_history_to_rag_service(monkeypatch):
    """验证 RAG 工具将当前查询和最近会话历史传给检索服务。"""
    captured = {}

    class FakeRagService:
        @staticmethod
        def build_context(query, source_filter, history):
            """捕获 RAG 上下文构建参数并返回固定证据内容。"""
            captured["query"] = query
            captured["source_filter"] = source_filter
            captured["history"] = history
            return SimpleNamespace(content="[证据:1] 测试资料", result=None)

    monkeypatch.setattr(agent_tools, "_get_rag_service", lambda: FakeRagService())
    context_token = agent_tools._user_context.set(
        {
            "user_id": 1,
            "retrieval_history": [
                {"role": "user", "content": "我刚才在问深蹲。"},
                {"role": "assistant", "content": "深蹲需要注意膝盖方向。"},
            ],
        }
    )
    try:
        result = agent_tools.rag_summarize.invoke({"query": "那深蹲呢？"})
    finally:
        agent_tools._user_context.reset(context_token)

    assert result == "[证据:1] 测试资料"
    assert captured["query"] == "那深蹲呢？"
    assert captured["source_filter"] is None
    assert captured["history"][0]["content"] == "我刚才在问深蹲。"


def test_build_evidence_cards_keeps_only_display_safe_hit_fields():
    """验证证据卡片只暴露前端展示所需的安全检索字段。"""
    hit = SimpleNamespace(
        rank=1,
        evidence_id="guide.md#chunk-1",
        source_id="guide.md",
        child_text="深蹲时膝盖跟随脚尖方向。",
        metadata={"tags": "动作,下肢"},
        rerank_score=0.9,
        score=0.03,
    )

    cards = agent_tools.build_evidence_cards(SimpleNamespace(hits=(hit,)))

    assert cards == [
        {
            "rank": 1,
            "evidence_id": "guide.md#chunk-1",
            "source_id": "guide.md",
            "snippet": "深蹲时膝盖跟随脚尖方向。",
            "tags": "动作,下肢",
            "score": 0.9,
        }
    ]


def test_direct_rag_graph_uses_latest_user_and_records_prior_history():
    """验证末尾有助手消息时仍检索最后用户问题及其之前的历史。"""

    captured = {}

    class FakeRagService:
        """提供无需外部服务的固定检索上下文。"""

        @staticmethod
        def build_context(query, history):
            """返回带单条证据的固定上下文。"""
            captured["query"] = query
            captured["history"] = history
            return SimpleNamespace(content="[证据:1] 深蹲资料", result="retrieval-result")

    class FakeModel:
        """提供无需真实模型的固定文本流。"""

        @staticmethod
        def stream(_messages):
            """返回带证据标记的固定回答。"""
            return [SimpleNamespace(content="膝盖跟随脚尖。[证据:1]")]

    executor = react_agent.DirectRagExecutor(
        model=FakeModel(),
        rag_service_factory=FakeRagService,
        evidence_builder=lambda _result: [{"rank": 1, "evidence_id": "guide.md#1"}],
    )
    graph = build_chat_routing_graph(classifier=FakeIntentClassifier())
    result = graph.invoke(
        build_initial_chat_state(
            messages=[
                {"role": "user", "content": "先说深蹲。"},
                {"role": "assistant", "content": "好的。"},
                {"role": "user", "content": "那膝盖呢？"},
                {"role": "assistant", "content": "我先想一下。"},
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

    assert result["retrieval_history"] == [
        {"role": "user", "content": "先说深蹲。"},
        {"role": "assistant", "content": "好的。"},
    ]
    assert captured == {
        "query": "那膝盖呢？",
        "history": [
            {"role": "user", "content": "先说深蹲。"},
            {"role": "assistant", "content": "好的。"},
        ],
    }
    assert json.loads(json.dumps(result, ensure_ascii=False))["rag_evidence"] == [
        {"rank": 1, "evidence_id": "guide.md#1"}
    ]
