"""聊天路由图与公开流式入口的兼容性测试。"""

import json
from types import SimpleNamespace

import pytest
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler

from app.services import react_agent
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

    def classify(self, _prompt, config=None):
        del config
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_structured_output_classifier_adapts_model_result():
    captured = {}

    class FakeStructuredModel:
        @staticmethod
        def invoke(prompt, config=None):
            captured["prompt"] = prompt
            captured["config"] = config
            return {"route": "direct_rag"}

    class FakeModel:
        @staticmethod
        def with_structured_output(schema):
            captured["schema"] = schema
            return FakeStructuredModel()

    config = {"callbacks": [RunCollectorCallbackHandler()]}
    decision = StructuredOutputIntentClassifier(FakeModel()).classify("分类问题", config=config)

    assert decision.route == "direct_rag"
    assert captured == {"schema": IntentDecision, "prompt": "分类问题", "config": config}


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


def _public_agent(classifier, *, direct_executor=None, inner_agent=None):
    """构造只含图执行依赖的公开入口测试 Agent。"""
    agent = object.__new__(ReactAgent)
    agent.direct_rag_executor = direct_executor
    agent.agent = inner_agent
    agent.max_steps = 8
    agent.max_tool_calls = 4
    agent.routing_graph = build_chat_routing_graph(classifier=classifier)
    return agent


def test_chat_sse_contract_is_unchanged_for_direct_rag_route():
    """直接 RAG 图路径仍应输出既有工具、证据和文本 JSON 行。"""

    class DirectExecutor:
        """返回固定且可序列化的直接检索事件。"""

        @staticmethod
        def stream(**_kwargs):
            """按既有 SSE 事件顺序生成响应。"""
            yield {"type": "tool", "name": "检索知识库"}
            yield {"type": "evidence", "items": [{"evidence_id": "guide#1"}]}
            yield {"type": "text", "content": "膝盖跟随脚尖。"}

    agent = _public_agent(
        FakeIntentClassifier(IntentDecision(route="direct_rag")),
        direct_executor=DirectExecutor(),
    )

    events = [
        json.loads(chunk)
        for chunk in agent.execute_stream(
            [{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
            user_id=7,
            session_id="stable-session",
        )
    ]

    assert events == [
        {"type": "tool", "name": "检索知识库"},
        {"type": "evidence", "items": [{"evidence_id": "guide#1"}]},
        {"type": "text", "content": "膝盖跟随脚尖。"},
    ]


def test_chat_sse_contract_is_unchanged_for_personalized_route():
    """个性化图路径仍应输出既有工具和文本 JSON 行。"""

    class PersonalizedInnerAgent:
        """模拟内层 Agent 的工具调用和最终文本。"""

        @staticmethod
        def stream(input_state, **_kwargs):
            """返回工具通知后可消费的最终文本。"""
            from langchain_core.messages import AIMessageChunk, ToolMessage

            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="", tool_call_chunks=[{"id": "call-1", "name": "get_user_profile"}]
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="画像已读取", tool_call_id="call-1"),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="为你安排每周三练。"),
                    {"langgraph_step": 2},
                ),
            )
            yield "values", {**input_state, "rag_evidence": [], "tool_call_count": 1}

    agent = _public_agent(
        FakeIntentClassifier(IntentDecision(route="personalized_agent")),
        inner_agent=PersonalizedInnerAgent(),
    )

    events = [
        json.loads(chunk)
        for chunk in agent.execute_stream(
            [{"role": "user", "content": "结合我的目标安排训练"}],
            user_id=7,
            session_id="stable-session",
        )
    ]

    assert events == [
        {"type": "tool", "name": "获取用户画像"},
        {"type": "text", "content": "为你安排每周三练。"},
    ]


def test_classifier_exception_returns_successful_personalized_sse_flow():
    """分类异常时必须保守回退，并保持个性化 SSE 成功输出。"""

    class PersonalizedInnerAgent:
        """提供分类回退后使用的固定文本流。"""

        @staticmethod
        def stream(input_state, **_kwargs):
            """返回个性化分支的最终文本。"""
            from langchain_core.messages import AIMessageChunk

            yield "messages", (AIMessageChunk(content="请补充你的训练频率。"), {})
            yield "values", {**input_state, "rag_evidence": [], "tool_call_count": 0}

    agent = _public_agent(
        FakeIntentClassifier(RuntimeError("classifier unavailable")),
        inner_agent=PersonalizedInnerAgent(),
    )

    events = [
        json.loads(chunk)
        for chunk in agent.execute_stream(
            [{"role": "user", "content": "深蹲怎么做？"}],
            user_id=7,
            session_id="stable-session",
        )
    ]

    assert events == [{"type": "text", "content": "请补充你的训练频率。"}]


def test_execute_stream_no_longer_uses_keyword_router():
    """公开入口不应再暴露由关键词决定的旧路由器。"""
    assert not hasattr(ReactAgent, "_should_use_direct_rag")


def test_execute_stream_passes_callback_config_to_routing_graph():
    """公开入口应将调用方配置原样传给 LangGraph 路由运行。"""
    captured = {}

    class FakeRoutingGraph:
        """捕获公开入口交给路由图的运行参数。"""

        @staticmethod
        def stream(_state, **kwargs):
            """记录配置后返回空事件流。"""
            captured.update(kwargs)
            return []

    agent = object.__new__(ReactAgent)
    agent.direct_rag_executor = object()
    agent.max_tool_calls = 4
    agent.routing_graph = FakeRoutingGraph()
    config = {"callbacks": [RunCollectorCallbackHandler()]}

    messages = [{"role": "user", "content": "深蹲怎么做？"}]

    assert list(agent.execute_stream(messages, config=config)) == []
    assert captured["config"] is config


def test_direct_rag_custom_stream_emits_tool_before_executor_error():
    """直接 RAG 抛错前已产生的工具事件必须立即到达公开流。"""

    class FailingDirectExecutor:
        """先产生工具事件，再模拟后续检索失败。"""

        @staticmethod
        def stream(**_kwargs):
            """验证图不会因后续异常吞掉首个事件。"""
            yield {"type": "tool", "name": "检索知识库"}
            raise RuntimeError("direct executor failed")

    agent = _public_agent(
        FakeIntentClassifier(IntentDecision(route="direct_rag")),
        direct_executor=FailingDirectExecutor(),
    )
    stream = agent.execute_stream(
        [{"role": "user", "content": "深蹲时膝盖应该朝哪里？"}],
        user_id=7,
        session_id="stable-session",
    )

    assert json.loads(next(stream)) == {"type": "tool", "name": "检索知识库"}
    with pytest.raises(RuntimeError, match="direct executor failed"):
        next(stream)


def test_personalized_custom_stream_emits_tool_before_executor_error():
    """个性化 Agent 抛错前已产生的工具事件必须立即到达公开流。"""

    class FailingInnerAgent:
        """先产生工具调用，再模拟后续模型执行失败。"""

        @staticmethod
        def stream(_input_state, **_kwargs):
            """验证内层 Agent 事件可穿过图的 custom 流。"""
            from langchain_core.messages import AIMessageChunk

            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"id": "call-1", "name": "get_user_profile"}],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            raise RuntimeError("personalized executor failed")

    agent = _public_agent(
        FakeIntentClassifier(IntentDecision(route="personalized_agent")),
        inner_agent=FailingInnerAgent(),
    )
    stream = agent.execute_stream(
        [{"role": "user", "content": "结合我的目标安排训练"}],
        user_id=7,
        session_id="stable-session",
    )

    assert json.loads(next(stream)) == {"type": "tool", "name": "获取用户画像"}
    with pytest.raises(RuntimeError, match="personalized executor failed"):
        next(stream)


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
        def stream(_messages, config=None):
            """返回一条带证据标记的固定模型输出。"""
            del config
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


def test_direct_rag_uses_collector_for_named_retrieval_runnable():
    """直接检索步骤应作为 agent_tool 运行被 Collector 捕获。"""

    class FakeRagService:
        """提供无需外部服务的固定检索上下文。"""

        @staticmethod
        def build_context(_query, history):
            """返回无证据的固定上下文。"""
            return SimpleNamespace(content="未检索到资料", result=None)

    class FakeModel:
        """提供无需真实模型的固定文本流。"""

        @staticmethod
        def stream(_messages, config=None):
            """返回固定回答分块。"""
            return [SimpleNamespace(content="暂时没有可靠证据。")]

    collector = RunCollectorCallbackHandler()
    executor = react_agent.DirectRagExecutor(
        model=FakeModel(),
        rag_service_factory=FakeRagService,
    )
    list(executor.stream(query="解释一下深蹲。", history=[], config={"callbacks": [collector]}))

    assert any(
        run.name == "rag_summarize" and "agent_tool" in run.tags
        for root in collector.traced_runs
        for run in [root, *root.child_runs]
    )


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
                dependencies=SimpleNamespace(direct_rag_executor=FakeDirectRagExecutor()),
            ),
        )
