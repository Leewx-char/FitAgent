"""Agent 知识库工具的会话历史传递测试。"""

from types import SimpleNamespace

from app.services import agent_tools


def test_rag_tool_forwards_recent_history_to_rag_service(monkeypatch):
    captured = {}

    class FakeRagService:
        @staticmethod
        def build_context(query, source_filter, history):
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
