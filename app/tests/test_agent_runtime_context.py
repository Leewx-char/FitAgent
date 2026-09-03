"""个性化 Agent 请求上下文与短期产物隔离测试。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from threading import Barrier

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.types import Command

from app.services import agent_tools, react_agent
from app.services.agent_trace import AgentTrace
from app.services.chat_routing_graph import (
    ChatRuntimeContext,
    IntentDecision,
    build_chat_routing_graph,
    build_initial_chat_state,
)
from app.services.react_agent import ReactAgent


class PersonalizedClassifier:
    """固定选择个性化分支。"""

    def classify(self, _prompt):
        """返回受约束的个性化路由。"""
        return IntentDecision(route="personalized_agent")


def _tool_runtime(*, user_id, city, history, call_id):
    context = ChatRuntimeContext(
        user_id=user_id,
        city=city,
        session_id=f"session-{user_id}",
        trace=AgentTrace(),
        dependencies=SimpleNamespace(max_tool_calls=4),
    )
    return ToolRuntime(
        state={"retrieval_history": history, "rag_evidence": [], "tool_call_count": 0},
        context=context,
        config={},
        stream_writer=lambda _event: None,
        tool_call_id=call_id,
        store=None,
    )


def test_personalized_graph_branch_invokes_existing_agent_with_runtime_context():
    """个性化外图节点应把同一请求上下文交给既有内层 Agent。"""
    captured = {}

    class FakeInnerAgent:
        @staticmethod
        def stream(input_state, **kwargs):
            captured.update({"input": input_state, **kwargs})
            yield "messages", (
                AIMessageChunk(content="个性化建议", id="answer-1"),
                {"langgraph_step": 1},
            )
            yield "values", {
                **input_state,
                "rag_evidence": [],
                "tool_call_count": 0,
            }

    personalized_executor = object.__new__(ReactAgent)
    personalized_executor.agent = FakeInnerAgent()
    personalized_executor.max_steps = 9
    personalized_executor.max_tool_calls = 4
    trace = AgentTrace(mode="direct_rag")
    runtime_context = ChatRuntimeContext(
        user_id=17,
        city="深圳",
        session_id="session-17",
        trace=trace,
        dependencies=SimpleNamespace(personalized_agent_executor=personalized_executor),
    )
    graph = build_chat_routing_graph(classifier=PersonalizedClassifier())

    result = graph.invoke(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "结合我的情况给建议"}],
            session_summary="近期每周训练三次。",
        ),
        context=runtime_context,
    )

    assert captured["context"] is runtime_context
    assert captured["config"] == {"recursion_limit": 9}
    assert captured["input"]["session_summary"] == "近期每周训练三次。"
    assert "user_id" not in captured["input"]
    assert "city" not in captured["input"]
    assert result["events"] == [{"type": "text", "content": "个性化建议"}]


def test_tool_runtime_reads_user_and_city_without_contextvar():
    """身份工具只从本次 ToolRuntime 读取用户与城市。"""
    runtime = _tool_runtime(user_id=23, city="成都", history=[], call_id="identity-23")

    assert agent_tools.get_user_id.func(runtime=runtime) == "23"
    assert agent_tools.get_user_location.func(runtime=runtime) == "成都"


def test_inner_agent_declares_runtime_context_and_short_term_state(monkeypatch):
    """内层 Agent 必须显式声明请求上下文和工具可更新的短期状态。"""
    captured = {}
    monkeypatch.setattr(
        react_agent,
        "get_settings",
        lambda: SimpleNamespace(agent_max_steps=8, agent_max_tool_calls=3),
    )
    monkeypatch.setattr(react_agent, "get_chat_model", lambda: object())
    monkeypatch.setattr(react_agent, "load_system_prompts", lambda: "system prompt")
    monkeypatch.setattr(
        react_agent,
        "create_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    ReactAgent()

    assert captured["context_schema"] is ChatRuntimeContext
    assert captured["state_schema"] is react_agent.PersonalizedAgentState


def test_parallel_requests_do_not_share_retrieval_history_or_evidence(monkeypatch):
    """两个交错请求不得串用画像、城市、检索历史或证据。"""
    barrier = Barrier(2)
    profiles = {
        31: SimpleNamespace(
            gender="女", age=28, height=165, weight=55, goal="增肌", weekly_days=3,
            experience="初级", injuries="[]", diet_restrict="[]", preferences='["瑜伽"]',
        ),
        47: SimpleNamespace(
            gender="男", age=36, height=180, weight=82, goal="减脂", weekly_days=4,
            experience="中级", injuries="[]", diet_restrict="[]", preferences='["跑步"]',
        ),
    }

    class FakeQuery:
        def __init__(self):
            self.user_id = None

        def filter(self, expression):
            self.user_id = expression.right.value
            return self

        def first(self):
            return profiles[self.user_id]

    class FakeDb:
        @staticmethod
        def query(_model):
            return FakeQuery()

    @contextmanager
    def fake_db_session():
        yield FakeDb()

    class FakeRagService:
        @staticmethod
        def build_context(query, source_filter, history):
            barrier.wait(timeout=3)
            evidence_id = f"{query}.md#1"
            hit = SimpleNamespace(
                rank=1,
                evidence_id=evidence_id,
                source_id=f"{query}.md",
                child_text=f"{query} 专属证据",
                metadata={"tags": query},
                rerank_score=0.9,
                score=0.1,
            )
            return SimpleNamespace(
                content=f"{query}|history={history[0]['content']}",
                result=SimpleNamespace(hits=(hit,)),
            )

    monkeypatch.setattr(agent_tools, "get_db_session", fake_db_session)
    monkeypatch.setattr(agent_tools, "_get_rag_service", lambda: FakeRagService())

    def run_request(user_id, city, query, history_text):
        runtime = _tool_runtime(
            user_id=user_id,
            city=city,
            history=[{"role": "user", "content": history_text}],
            call_id=f"rag-{user_id}",
        )
        profile = agent_tools.get_user_profile.func(runtime=runtime)
        location = agent_tools.get_user_location.func(runtime=runtime)
        command = agent_tools.rag_summarize.func(query=query, runtime=runtime)
        assert isinstance(command, Command)
        message = command.update["messages"][0]
        assert isinstance(message, ToolMessage)
        return profile, location, message.content, command.update["rag_evidence"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_request, 31, "杭州", "深蹲", "A 的历史")
        second = pool.submit(run_request, 47, "北京", "跑步", "B 的历史")
        result_a, result_b = first.result(timeout=5), second.result(timeout=5)

    assert "增肌" in result_a[0] and "减脂" not in result_a[0]
    assert "减脂" in result_b[0] and "增肌" not in result_b[0]
    assert result_a[1] == "杭州"
    assert result_b[1] == "北京"
    assert result_a[2] == "深蹲|history=A 的历史"
    assert result_b[2] == "跑步|history=B 的历史"
    assert result_a[3][0]["evidence_id"] == "深蹲.md#1"
    assert result_b[3][0]["evidence_id"] == "跑步.md#1"


def test_personalized_branch_marks_trace_mode_agent():
    """个性化分支执行前应把轨迹模式标记为 agent。"""
    class EmptyInnerAgent:
        @staticmethod
        def stream(input_state, **_kwargs):
            yield "values", input_state

    executor = object.__new__(ReactAgent)
    executor.agent = EmptyInnerAgent()
    executor.max_steps = 5
    executor.max_tool_calls = 2
    trace = AgentTrace(mode="direct_rag")
    context = ChatRuntimeContext(
        user_id=9,
        city="",
        session_id="session-9",
        trace=trace,
        dependencies=SimpleNamespace(personalized_agent_executor=executor),
    )

    build_chat_routing_graph(classifier=PersonalizedClassifier()).invoke(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "给我一个计划"}],
            session_summary="",
        ),
        context=context,
    )

    assert trace.mode == "agent"


def test_personalized_agent_keeps_tool_and_evidence_events():
    """内层 Agent 的工具与证据事件应留在本次个性化图状态中。"""
    class ToolCallingAgent:
        @staticmethod
        def stream(input_state, **_kwargs):
            yield "messages", (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": "rag_summarize", "id": "rag-call"}],
                ),
                {"langgraph_step": 1},
            )
            yield "messages", (
                ToolMessage(content="[证据:1] 深蹲资料", tool_call_id="rag-call"),
                {"langgraph_step": 1},
            )
            yield "values", {
                **input_state,
                "rag_evidence": [{"rank": 1, "evidence_id": "guide.md#1"}],
                "tool_call_count": 1,
            }
            yield "messages", (
                AIMessageChunk(content="膝盖跟随脚尖。", id="answer-1"),
                {"langgraph_step": 2},
            )

    executor = object.__new__(ReactAgent)
    executor.agent = ToolCallingAgent()
    executor.max_steps = 5
    result = executor.stream_personalized_events(
        build_initial_chat_state(
            messages=[{"role": "user", "content": "深蹲怎么做？"}],
            session_summary="",
        ),
        ChatRuntimeContext(
            user_id=5,
            city="",
            session_id="session-5",
            trace=AgentTrace(),
            dependencies=SimpleNamespace(max_tool_calls=2),
        ),
    )

    assert result["events"] == [
        {"type": "tool", "name": "检索知识库"},
        {"type": "evidence", "items": [{"rank": 1, "evidence_id": "guide.md#1"}]},
        {"type": "text", "content": "膝盖跟随脚尖。"},
    ]
    assert result["tool_call_count"] == 1
