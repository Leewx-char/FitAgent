"""LangGraph 聊天路由图的行为测试。"""

import json
from pathlib import Path

from app.services.chat_routing_graph import (
    ChatRuntimeContext,
    IntentDecision,
    build_chat_routing_graph,
    build_initial_chat_state,
    classify_intent,
)


class CapturingClassifier:
    """返回指定决策并保存收到的分类提示词。"""

    def __init__(self, decision) -> None:
        self.decision = decision
        self.prompts = []

    def classify(self, prompt: str, config=None):
        """记录提示词并返回固定结果或抛出固定异常。"""
        del config
        self.prompts.append(prompt)
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


def _runtime_context() -> ChatRuntimeContext:
    return ChatRuntimeContext(
        user_id=1,
        session_id="session-1",
        dependencies={"api_key": "secret-value"},
    )


def test_classifier_uses_only_last_six_messages_and_keeps_in_window_injury_context():
    classifier = CapturingClassifier(IntentDecision(route="personalized_agent"))
    messages = [
        {"role": "user", "content": "第 1 轮的旧城市是成都"},
        {"role": "assistant", "content": "第 1 轮回复"},
        {"role": "user", "content": "第 2 轮问题"},
        {"role": "assistant", "content": "第 2 轮回复"},
        {"role": "user", "content": "三轮内的膝盖疼"},
        {"role": "assistant", "content": "第 3 轮回复"},
        {"role": "user", "content": "那我今天怎么练？"},
    ]

    route = classify_intent(build_initial_chat_state(messages), classifier)

    assert route == "personalized_agent"
    prompt = classifier.prompts[0]
    assert "第 1 轮的旧城市" not in prompt
    assert "三轮内的膝盖疼" in prompt
    assert prompt.count("[user]") + prompt.count("[assistant]") == 6


def test_generic_question_can_use_direct_rag_despite_old_personal_history():
    classifier = CapturingClassifier(IntentDecision(route="direct_rag"))
    messages = [
        {"role": "user", "content": "我住在成都"},
        {"role": "assistant", "content": "知道了"},
        {"role": "user", "content": "换个话题"},
        {"role": "assistant", "content": "可以"},
        {"role": "user", "content": "先不用结合我"},
        {"role": "assistant", "content": "明白"},
        {"role": "user", "content": "深蹲主要锻炼哪些肌肉？"},
    ]

    route = classify_intent(build_initial_chat_state(messages), classifier)

    assert route == "direct_rag"
    assert "我住在成都" not in classifier.prompts[0]


def test_build_initial_state_and_runtime_have_no_facts_summary_or_city():
    state = build_initial_chat_state(
        messages=[
            {"role": "user", "content": "  我想减脂，膝盖不舒服。  "},
            {"role": "assistant", "content": "收到"},
        ]
    )
    context = _runtime_context()

    assert state == {
        "messages": [
            {"role": "user", "content": "我想减脂，膝盖不舒服。"},
            {"role": "assistant", "content": "收到"},
        ],
        "retrieval_history": [],
        "route": None,
        "rag_evidence": [],
        "events": [],
    }
    assert not hasattr(context, "city")


def test_classifier_failure_falls_back_to_personalized_agent():
    classifier = CapturingClassifier(RuntimeError("classifier unavailable"))

    route = classify_intent(
        build_initial_chat_state([{"role": "user", "content": "深蹲怎么做？"}]),
        classifier,
    )

    assert route == "personalized_agent"


def test_graph_selects_direct_rag_edge_for_generic_intent():
    graph = build_chat_routing_graph(
        classifier=CapturingClassifier(IntentDecision(route="direct_rag")),
        direct_rag_node=lambda _state, runtime, config: {"events": [{"branch": "direct_rag"}]},
        personalized_agent_node=lambda _state, runtime, config: {"events": [{"branch": "agent"}]},
    )

    result = graph.invoke(
        build_initial_chat_state(messages=[{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}]),
        context=_runtime_context(),
    )

    assert result["route"] == "direct_rag"
    assert result["events"] == [{"branch": "direct_rag"}]


def test_graph_selects_personalized_agent_edge_for_personal_intent():
    graph = build_chat_routing_graph(
        classifier=CapturingClassifier(IntentDecision(route="personalized_agent")),
        direct_rag_node=lambda _state, runtime, config: {"events": [{"branch": "direct_rag"}]},
        personalized_agent_node=lambda _state, runtime, config: {"events": [{"branch": "agent"}]},
    )

    result = graph.invoke(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "结合我的体重安排减脂训练。"}]
        ),
        context=_runtime_context(),
    )

    assert result["route"] == "personalized_agent"
    assert result["events"] == [{"branch": "agent"}]


def test_state_does_not_contain_runtime_identity_or_secret_values():
    state = build_initial_chat_state(
        messages=[{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}]
    )

    serialized_state = json.dumps(state, ensure_ascii=False)

    assert "u-1" not in serialized_state
    assert "secret-value" not in serialized_state
    assert "user_id" not in state
    assert "session_id" not in state


def test_graph_node_receives_runtime_context_from_graph_invocation():
    received_contexts = []

    def direct_rag_node(_state, runtime, config):
        """记录图运行时上下文，同时接受回调配置。"""
        del config
        received_contexts.append(runtime.context)
        return {"events": [{"branch": "direct_rag"}]}

    graph = build_chat_routing_graph(
        classifier=CapturingClassifier(IntentDecision(route="direct_rag")),
        direct_rag_node=direct_rag_node,
    )
    runtime_context = _runtime_context()

    result = graph.invoke(
        build_initial_chat_state(messages=[{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}]),
        context=runtime_context,
    )

    assert received_contexts == [runtime_context]
    assert "u-1" not in json.dumps(result, ensure_ascii=False)
    assert "secret-value" not in json.dumps(result, ensure_ascii=False)


def test_active_docs_do_not_describe_removed_session_facts():
    """Active architecture docs describe the current three-layer context boundary."""
    root = Path(__file__).resolve().parents[2]
    document_paths = [
        root / "README.md",
        root / "docs/langgraph-chat-routing.md",
        root / "docs/learning-guide.md",
        root / "docs/memory-architecture.md",
        root / "docs/interview/常见面试题.md",
        root / "docs/interview/技术亮点.md",
    ]
    required_terms = [
        "当前会话最近 20 条原始消息",
        "分类器仅见最新 6 条",
        "MySQL session_summaries v3 缓存",
        "当前窗口不足以解释早期引用时，Agent 按需调用 get_session_summary",
        "压缩早期全部已存储消息",
        "不按角色过滤",
        "模型结合当前系统提示词、最近消息和早期摘要综合判断",
        "mem0",
        "用户消息提取为 proposed",
        "get_confirmed_memories(query)",
        "只读 confirmed、未过期结果",
    ]

    for document_path in document_paths:
        content = document_path.read_text(encoding="utf-8")
        assert "session_facts" not in content, document_path
        assert "确定性提取" not in content, document_path
        assert "session_summaries v2" not in content, document_path
        assert "只压缩早期 user 消息" not in content, document_path
        assert "不可信任务上下文" not in content, document_path
        assert "不能发出指令" not in content, document_path
        assert "当前系统规则或最近消息冲突时以后者为准" not in content, document_path
        for term in required_terms:
            assert term in content, f"{document_path} is missing: {term}"
