"""LangGraph 聊天路由图的行为测试。"""

import json

from app.services.chat_routing_graph import (
    ChatRuntimeContext,
    IntentDecision,
    build_chat_routing_graph,
    build_initial_chat_state,
)


class FakeIntentClassifier:
    def __init__(self, route: str) -> None:
        self._route = route

    def classify(self, _prompt: str) -> IntentDecision:
        return IntentDecision(route=self._route)


def _runtime_context() -> ChatRuntimeContext:
    return ChatRuntimeContext(
        user_id="u-1",
        city="上海",
        session_id="session-1",
        trace=object(),
        dependencies={"api_key": "secret-value"},
    )


def test_build_initial_state_writes_session_facts_and_empty_artifacts():
    state = build_initial_chat_state(
        messages=[
            {"role": "user", "content": "  我想减脂，膝盖不舒服。  "},
            {"role": "assistant", "content": "收到"},
        ],
        session_summary="此前讨论过训练频率。",
    )

    assert state == {
        "messages": [
            {"role": "user", "content": "我想减脂，膝盖不舒服。"},
            {"role": "assistant", "content": "收到"},
        ],
        "session_facts": {"training_goal": "减脂", "injuries": ["膝盖伤"]},
        "session_summary": "此前讨论过训练频率。",
        "retrieval_history": [],
        "route": None,
        "rag_evidence": [],
        "tool_call_count": 0,
        "events": [],
    }


def test_graph_selects_direct_rag_edge_for_generic_intent():
    graph = build_chat_routing_graph(
        classifier=FakeIntentClassifier("direct_rag"),
        direct_rag_node=lambda _state, runtime: {"events": [{"branch": "direct_rag"}]},
        personalized_agent_node=lambda _state, runtime: {"events": [{"branch": "agent"}]},
    )

    result = graph.invoke(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
            session_summary="",
        ),
        context=_runtime_context(),
    )

    assert result["route"] == "direct_rag"
    assert result["events"] == [{"branch": "direct_rag"}]


def test_graph_selects_personalized_agent_edge_for_personal_intent():
    graph = build_chat_routing_graph(
        classifier=FakeIntentClassifier("personalized_agent"),
        direct_rag_node=lambda _state, runtime: {"events": [{"branch": "direct_rag"}]},
        personalized_agent_node=lambda _state, runtime: {"events": [{"branch": "agent"}]},
    )

    result = graph.invoke(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "结合我的体重安排减脂训练。"}],
            session_summary="",
        ),
        context=_runtime_context(),
    )

    assert result["route"] == "personalized_agent"
    assert result["events"] == [{"branch": "agent"}]


def test_state_does_not_contain_runtime_identity_or_secret_values():
    state = build_initial_chat_state(
        messages=[{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
        session_summary="",
    )

    serialized_state = json.dumps(state, ensure_ascii=False)

    assert "u-1" not in serialized_state
    assert "secret-value" not in serialized_state
    assert "user_id" not in state
    assert "session_id" not in state


def test_graph_node_receives_runtime_context_from_graph_invocation():
    received_contexts = []

    def direct_rag_node(_state, runtime):
        received_contexts.append(runtime.context)
        return {"events": [{"branch": "direct_rag"}]}

    graph = build_chat_routing_graph(
        classifier=FakeIntentClassifier("direct_rag"),
        direct_rag_node=direct_rag_node,
    )
    runtime_context = _runtime_context()

    result = graph.invoke(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
            session_summary="",
        ),
        context=runtime_context,
    )

    assert received_contexts == [runtime_context]
    assert "u-1" not in json.dumps(result, ensure_ascii=False)
    assert "secret-value" not in json.dumps(result, ensure_ascii=False)
