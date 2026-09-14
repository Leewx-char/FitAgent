"""个性化 Agent 请求上下文与短期产物隔离测试。"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from threading import Barrier

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langgraph.types import Command

from app.services import agent_tools, react_agent
from app.services.middleware import monitor_tool
from langchain.tools.tool_node import ToolCallRequest
from app.services.chat_routing_graph import (
    ChatRuntimeContext,
    IntentDecision,
    build_chat_routing_graph,
    build_initial_chat_state,
)
from app.services.react_agent import ReactAgent


class _LegacyAstreamEventAdapter:
    """仅在旧测试替身中，把双通道产物转换为 v2 事件。"""

    def __init__(self, agent):
        """保存仍实现旧 astream 的历史测试替身。"""
        self._agent = agent

    async def astream_events(self, input_state, *, version, **kwargs):
        """让历史断言继续覆盖对外 SSE 语义，而非生产兼容分支。"""
        assert version == "v2"
        tool_names = {}
        completed_tool_ids = set()
        pending_tool_ids = []
        emitted_evidence = list(input_state.get("rag_evidence", []))

        def completed_events(evidence):
            """将已观察到的工具结果转换为一次结束事件。"""
            nonlocal emitted_evidence
            new_evidence = evidence[len(emitted_evidence) :]
            result = []
            for tool_id in pending_tool_ids[:]:
                tool_name = tool_names.get(tool_id, "unknown")
                output = Command(update={"rag_evidence": new_evidence})
                if not new_evidence:
                    output = "tool-result"
                result.append(
                    {
                        "event": "on_tool_end",
                        "run_id": tool_id,
                        "name": "rag_summarize" if new_evidence else tool_name,
                        "data": {"output": output},
                    }
                )
                pending_tool_ids.remove(tool_id)
                completed_tool_ids.add(tool_id)
            emitted_evidence = list(evidence)
            return result

        async for stream_mode, payload in self._agent.astream(
            input_state,
            stream_mode=["messages", "values"],
            **kwargs,
        ):
            if stream_mode == "messages":
                for event in completed_events(emitted_evidence):
                    yield event
                message, _metadata = payload
                if isinstance(message, (AIMessage, AIMessageChunk)):
                    tool_calls = (
                        getattr(message, "tool_call_chunks", None)
                        or getattr(message, "tool_calls", None)
                        or []
                    )
                    for tool_call in tool_calls:
                        tool_id = tool_call.get("id") if isinstance(tool_call, dict) else None
                        tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
                        if not isinstance(tool_id, str) or not isinstance(tool_name, str):
                            continue
                        tool_names[tool_id] = tool_name
                        yield {
                            "event": "on_tool_start",
                            "run_id": tool_id,
                            "name": tool_name,
                            "data": {"input": {}},
                        }
                    if message.content:
                        yield {
                            "event": "on_chat_model_stream",
                            "run_id": str(getattr(message, "id", "model")),
                            "name": "legacy-model",
                            "data": {"chunk": message},
                        }
                elif isinstance(message, ToolMessage):
                    pending_tool_ids.append(message.tool_call_id)
            elif stream_mode == "values":
                messages = payload.get("messages", [])
                for message in messages:
                    if isinstance(message, (AIMessage, AIMessageChunk)):
                        tool_calls = (
                            getattr(message, "tool_call_chunks", None)
                            or getattr(message, "tool_calls", None)
                            or []
                        )
                        for tool_call in tool_calls:
                            tool_id = tool_call.get("id") if isinstance(tool_call, dict) else None
                            tool_name = (
                                tool_call.get("name") if isinstance(tool_call, dict) else None
                            )
                            if (
                                not isinstance(tool_id, str)
                                or not isinstance(tool_name, str)
                                or tool_id in tool_names
                            ):
                                continue
                            tool_names[tool_id] = tool_name
                            yield {
                                "event": "on_tool_start",
                                "run_id": tool_id,
                                "name": tool_name,
                                "data": {"input": {}},
                            }
                    elif (
                        isinstance(message, ToolMessage)
                        and message.tool_call_id not in completed_tool_ids
                        and message.tool_call_id not in pending_tool_ids
                    ):
                        pending_tool_ids.append(message.tool_call_id)
                for event in completed_events(payload.get("rag_evidence", [])):
                    yield event
                last_message = messages[-1] if messages else None
                if isinstance(last_message, AIMessage) and not last_message.tool_calls:
                    yield {
                        "event": "on_chat_model_end",
                        "run_id": str(last_message.id or "model"),
                        "name": "legacy-model",
                        "data": {"output": last_message},
                    }
        for event in completed_events(emitted_evidence):
            yield event


class _AgentDescriptor:
    """为旧测试替身注入 v2 事件入口，不改变生产实现。"""

    def __get__(self, instance, _owner):
        """返回实例中保存的原始或转换后的 Agent。"""
        if instance is None:
            return self
        return instance.__dict__["_test_agent"]

    def __set__(self, instance, value):
        """仅包裹缺少 astream_events 的历史测试替身。"""
        instance.__dict__["_test_agent"] = (
            value if hasattr(value, "astream_events") else _LegacyAstreamEventAdapter(value)
        )


@pytest.fixture(autouse=True)
def _adapt_legacy_agent_test_doubles(monkeypatch):
    """在本测试模块内隔离旧替身，生产代码始终只调用 v2 事件流。"""
    monkeypatch.setattr(ReactAgent, "agent", _AgentDescriptor(), raising=False)


class PersonalizedClassifier:
    """固定选择个性化分支。"""

    def classify(self, _prompt, config=None):
        """返回受约束的个性化路由。"""
        del config
        return IntentDecision(route="personalized_agent")


def _tool_runtime(*, user_id, history, call_id):
    context = ChatRuntimeContext(
        user_id=user_id,
        session_id=f"session-{user_id}",
        dependencies=SimpleNamespace(max_tool_calls=4),
    )
    return ToolRuntime(
        state={"retrieval_history": history, "rag_evidence": []},
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
        async def astream(input_state, **kwargs):
            captured.update({"input": input_state, **kwargs})
            yield (
                "messages",
                (
                    AIMessageChunk(content="个性化建议", id="answer-1"),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "values",
                {
                    **input_state,
                    "rag_evidence": [],
                },
            )

    personalized_executor = object.__new__(ReactAgent)
    personalized_executor.agent = FakeInnerAgent()
    personalized_executor.max_steps = 9
    personalized_executor.max_tool_calls = 4
    runtime_context = ChatRuntimeContext(
        user_id=17,
        session_id="session-17",
        dependencies=SimpleNamespace(personalized_agent_executor=personalized_executor),
    )
    graph = build_chat_routing_graph(classifier=PersonalizedClassifier())
    collector = RunCollectorCallbackHandler()

    result = asyncio.run(
        graph.ainvoke(
            build_initial_chat_state(
                messages=[
                    {"role": "user", "content": "我之前练过深蹲。"},
                    {"role": "assistant", "content": "注意膝盖方向。"},
                    {"role": "user", "content": "结合我的情况给建议"},
                ]
            ),
            context=runtime_context,
            config={"callbacks": [collector]},
        )
    )

    assert captured["context"] is runtime_context
    assert captured["config"]["recursion_limit"] == 9
    assert collector in captured["config"]["callbacks"].handlers
    assert "session_facts" not in captured["input"]
    assert "session_summary" not in captured["input"]
    assert captured["input"]["retrieval_history"] == [
        {"role": "user", "content": "我之前练过深蹲。"},
        {"role": "assistant", "content": "注意膝盖方向。"},
    ]
    assert "tool_call_limit" not in captured["input"]
    assert "user_id" not in captured["input"]
    assert "city" not in captured["input"]
    assert not hasattr(runtime_context, "trace")
    assert result["events"] == [{"type": "text", "content": "个性化建议"}]


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
    assert react_agent.TOOL_DISPLAY["get_session_summary"] == "读取早期会话摘要"
    assert "get_session_summary" in {tool.name for tool in captured["tools"]}
    assert "get_user_id" not in react_agent.TOOL_DISPLAY
    assert "get_user_id" not in {tool.name for tool in captured["tools"]}
    assert "get_user_location" not in react_agent.TOOL_DISPLAY
    assert "get_user_location" not in {tool.name for tool in captured["tools"]}


def test_parallel_requests_do_not_share_profile_query_or_evidence(monkeypatch):
    """两个交错请求不得串用画像、查询或证据。"""
    barrier = Barrier(2)
    profiles = {
        31: SimpleNamespace(
            gender="女",
            age=28,
            height=165,
            weight=55,
            goal="增肌",
            weekly_days=3,
            experience="初级",
            injuries="[]",
            diet_restrict="[]",
            preferences='["瑜伽"]',
        ),
        47: SimpleNamespace(
            gender="男",
            age=36,
            height=180,
            weight=82,
            goal="减脂",
            weekly_days=4,
            experience="中级",
            injuries="[]",
            diet_restrict="[]",
            preferences='["跑步"]',
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
        def build_context(query, source_filter):
            barrier.wait(timeout=3)
            evidence_id = f"{query}.md#1"
            hit = SimpleNamespace(
                rank=1,
                evidence_id=evidence_id,
                source_id=f"{query}.md",
                text=f"{query} 专属证据",
                metadata={"tags": query},
                score=0.1,
            )
            return SimpleNamespace(
                content=f"{query} 专属证据",
                result=SimpleNamespace(hits=(hit,)),
            )

    monkeypatch.setattr(agent_tools, "get_db_session", fake_db_session)
    monkeypatch.setattr(agent_tools, "_get_rag_service", lambda: FakeRagService())

    def run_request(user_id, query, history_text):
        runtime = _tool_runtime(
            user_id=user_id,
            history=[{"role": "user", "content": history_text}],
            call_id=f"rag-{user_id}",
        )
        profile = agent_tools.get_user_profile.func(runtime=runtime)
        command = agent_tools.rag_summarize.func(query=query, runtime=runtime)
        assert isinstance(command, Command)
        message = command.update["messages"][0]
        assert isinstance(message, ToolMessage)
        return profile, message.content, command.update["rag_evidence"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_request, 31, "深蹲", "A 的历史")
        second = pool.submit(run_request, 47, "跑步", "B 的历史")
        result_a, result_b = first.result(timeout=5), second.result(timeout=5)

    assert "增肌" in result_a[0] and "减脂" not in result_a[0]
    assert "减脂" in result_b[0] and "增肌" not in result_b[0]
    assert result_a[1] == "深蹲 专属证据"
    assert result_b[1] == "跑步 专属证据"
    assert result_a[2][0]["evidence_id"] == "深蹲.md#1"
    assert result_b[2][0]["evidence_id"] == "跑步.md#1"


def test_personalized_branch_uses_context_without_trace_field():
    """个性化分支应仅依赖不含 trace 的请求上下文。"""

    class EmptyInnerAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            yield "values", input_state

    executor = object.__new__(ReactAgent)
    executor.agent = EmptyInnerAgent()
    executor.max_steps = 5
    executor.max_tool_calls = 2
    context = ChatRuntimeContext(
        user_id=9,
        session_id="session-9",
        dependencies=SimpleNamespace(personalized_agent_executor=executor),
    )

    asyncio.run(
        build_chat_routing_graph(classifier=PersonalizedClassifier()).ainvoke(
            build_initial_chat_state(messages=[{"role": "user", "content": "给我一个计划"}]),
            context=context,
        )
    )

    assert not hasattr(context, "trace")


def test_personalized_agent_keeps_tool_and_evidence_events():
    """内层 Agent 的工具与证据事件应留在本次个性化图状态中。"""

    class ToolCallingAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": "rag_summarize", "id": "rag-call"}],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="[证据:1] 深蹲资料", tool_call_id="rag-call"),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "values",
                {
                    **input_state,
                    "rag_evidence": [{"rank": 1, "evidence_id": "guide.md#1"}],
                },
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="膝盖跟随脚尖。", id="answer-1"),
                    {"langgraph_step": 2},
                ),
            )

    executor = object.__new__(ReactAgent)
    executor.agent = ToolCallingAgent()
    executor.max_steps = 5
    executor.max_tool_calls = 2
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "深蹲怎么做？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=2),
            ),
        )
    )

    assert result["events"] == [
        {"type": "tool", "id": "rag-call", "name": "检索知识库"},
        {"type": "tool_completed", "id": "rag-call"},
        {"type": "evidence", "items": [{"rank": 1, "evidence_id": "guide.md#1"}]},
        {"type": "text", "content": "膝盖跟随脚尖。"},
    ]


def test_personalized_agent_emits_final_ai_message_after_tool():
    """工具结果后的完整 AIMessage 必须转发为 SSE 文本事件。"""

    class ToolThenFinalMessageAgent:
        @staticmethod
        async def astream(_input_state, **_kwargs):
            """模拟工具完成后以完整 AIMessage 返回正式回答。"""
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": "get_weather", "id": "weather-call"}],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="广州晴", tool_call_id="weather-call"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    AIMessage(content="建议傍晚慢跑 30 分钟。", id="answer-1"),
                    {"langgraph_step": 3},
                ),
            )

    executor = object.__new__(ReactAgent)
    executor.agent = ToolThenFinalMessageAgent()
    executor.max_steps = 30
    executor.max_tool_calls = 10
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=10),
            ),
        )
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-call", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-call"},
        {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
    ]


def test_personalized_agent_accepts_complete_ai_message_tool_calls():
    """非流式工具调用也必须与分片工具事件保持一致。"""

    class CompleteMessageToolAgent:
        @staticmethod
        async def astream(_input_state, **_kwargs):
            """模拟完整消息声明工具后返回正式回答。"""
            yield (
                "messages",
                (
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "get_weather", "args": {}, "id": "weather-call"}],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="广州晴", tool_call_id="weather-call"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    AIMessage(content="建议傍晚慢跑 30 分钟。", id="answer-1"),
                    {"langgraph_step": 3},
                ),
            )

    executor = object.__new__(ReactAgent)
    executor.agent = CompleteMessageToolAgent()
    executor.max_steps = 30
    executor.max_tool_calls = 10
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=10),
            ),
        )
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-call", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-call"},
        {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
    ]


def test_personalized_agent_uses_values_for_tool_completion_and_final_text():
    """消息分片为空时，必须从 values 补齐工具完成与最终回答。"""

    class ValuesOnlyResultAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            """模拟 DeepSeek 将工具结果和正式回答仅写入 values 状态。"""
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            tool_state = {
                **input_state,
                "messages": [
                    *input_state["messages"],
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "get_weather", "args": {}, "id": "weather-call"},
                            {"name": "get_current_month", "args": {}, "id": "month-call"},
                        ],
                    ),
                    ToolMessage(content="广州晴", tool_call_id="weather-call"),
                    ToolMessage(content="9 月", tool_call_id="month-call"),
                ],
            }
            yield ("values", tool_state)
            yield (
                "messages",
                (
                    AIMessageChunk(content="", id="answer-1", chunk_position="last"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "values",
                {
                    **tool_state,
                    "messages": [
                        *tool_state["messages"],
                        AIMessage(content="建议傍晚慢跑 30 分钟。", id="answer-1"),
                    ],
                },
            )

    executor = object.__new__(ReactAgent)
    executor.agent = ValuesOnlyResultAgent()
    executor.max_steps = 30
    executor.max_tool_calls = 10
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=10),
            ),
        )
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-call", "name": "查询天气"},
        {"type": "tool", "id": "month-call", "name": "获取月份"},
        {"type": "tool_completed", "id": "weather-call"},
        {"type": "tool_completed", "id": "month-call"},
        {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
    ]


def test_personalized_agent_logs_empty_final_message(caplog):
    """工具链结束后的空最终消息必须留下可关联的诊断记录。"""

    class EmptyFinalMessageAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            """模拟模型正常结束但没有返回正文的异常边界。"""
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "get_weather",
                                "id": "weather-call",
                                "args": '{"city":"广州","token":"test-secret"}',
                            }
                        ],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(
                        content="广州晴，token=test-secret",
                        tool_call_id="weather-call",
                    ),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (AIMessage(content="", id="answer-1"), {"langgraph_step": 2}),
            )
            yield (
                "values",
                {**input_state, "messages": [*input_state["messages"], AIMessage(content="")]},
            )

    executor = object.__new__(ReactAgent)
    executor.agent = EmptyFinalMessageAgent()
    executor.max_steps = 30
    executor.max_tool_calls = 10
    caplog.set_level(logging.INFO, logger="agent")

    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=10),
            ),
        )
    )
    assert result["events"] == [
        {"type": "tool", "id": "weather-call", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-call"},
    ]
    assert any(
        "AGENT_STREAM_EVENT" in record.message
        and '"event": "on_chat_model_end"' in record.message
        and '"content_chars": 0' in record.message
        for record in caplog.records
    )
    assert all(
        "广州" not in record.message and "test-secret" not in record.message
        for record in caplog.records
    )


def test_personalized_agent_logs_only_terminal_stream_chunk(caplog):
    """分片流只应记录终止边界，避免空分片淹没诊断日志。"""

    class StreamChunkAgent:
        @staticmethod
        async def astream(_input_state, **_kwargs):
            """模拟一轮带中间空分片与终止分片的模型流。"""
            yield (
                "messages",
                (AIMessageChunk(content="", id="answer-1"), {"langgraph_step": 2}),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="", id="answer-1", chunk_position="last"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "values",
                {
                    **_input_state,
                    "messages": [
                        *_input_state["messages"],
                        AIMessage(content="最终回答", id="answer-1"),
                    ],
                },
            )

    executor = object.__new__(ReactAgent)
    executor.agent = StreamChunkAgent()
    executor.max_steps = 30
    executor.max_tool_calls = 10
    caplog.set_level(logging.INFO, logger="agent")

    asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=10),
            ),
        )
    )

    stream_logs = [record for record in caplog.records if "AGENT_STREAM_EVENT" in record.message]
    assert len(stream_logs) == 1
    assert '"event": "on_chat_model_end"' in stream_logs[0].message


def test_personalized_agent_logs_summary_when_stream_fails(caplog):
    """流异常时也必须记录不含异常原文的事件摘要。"""

    class FailingAgent:
        @staticmethod
        async def astream(_input_state, **_kwargs):
            """模拟模型流在工具后异常中断。"""
            if False:
                yield None
            raise RuntimeError("test-secret")

    executor = object.__new__(ReactAgent)
    executor.agent = FailingAgent()
    executor.max_steps = 30
    executor.max_tool_calls = 10
    caplog.set_level(logging.INFO, logger="agent")

    with pytest.raises(RuntimeError, match="test-secret"):
        asyncio.run(
            executor.astream_personalized_events(
                build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
                ChatRuntimeContext(
                    user_id=5,
                    session_id="session-5",
                    dependencies=SimpleNamespace(max_tool_calls=10),
                ),
            )
        )

    assert any(
        "AGENT_STREAM_SUMMARY" in record.message
        and '"outcome": "failed"' in record.message
        and '"error_type": "RuntimeError"' in record.message
        for record in caplog.records
    )
    assert all("test-secret" not in record.message for record in caplog.records)


def test_personalized_agent_resets_preamble_and_emits_final_text_after_tool():
    """工具调用前的说明必须撤回，工具后的同一步号文本仍应透传。"""

    class PreambleThenToolAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            yield (
                "messages",
                (
                    AIMessageChunk(content="I'll check the weather first.", id="draft-1"),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": "get_weather", "id": "weather-call"}],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="广州晴", tool_call_id="weather-call"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="建议傍晚慢跑 30 分钟。", id="answer-1"),
                    {"langgraph_step": 2},
                ),
            )

    executor = object.__new__(ReactAgent)
    executor.agent = PreambleThenToolAgent()
    executor.max_steps = 5
    executor.max_tool_calls = 2
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=2),
            ),
        )
    )

    assert result["events"] == [
        {"type": "text", "content": "I'll check the weather first."},
        {"type": "text_reset"},
        {"type": "tool", "id": "weather-call", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-call"},
        {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
    ]


def test_personalized_agent_waits_for_all_parallel_tools_before_final_text():
    """并行工具尚未全部返回时不得输出最终回答，避免答案抢在工具链之前展示。"""

    class ParallelToolAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {"name": "get_weather", "id": "weather-call"},
                            {"name": "get_current_month", "id": "month-call"},
                        ],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="九月", tool_call_id="month-call"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="不应提前输出。", id="premature-1"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="广州晴", tool_call_id="weather-call"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="建议清晨进行轻松跑。", id="answer-1"),
                    {"langgraph_step": 2},
                ),
            )

    executor = object.__new__(ReactAgent)
    executor.agent = ParallelToolAgent()
    executor.max_steps = 5
    executor.max_tool_calls = 2
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=2),
            ),
        )
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-call", "name": "查询天气"},
        {"type": "tool", "id": "month-call", "name": "获取月份"},
        {"type": "tool_completed", "id": "month-call"},
        {"type": "tool_completed", "id": "weather-call"},
        {"type": "text", "content": "建议清晨进行轻松跑。"},
    ]


def test_personalized_agent_resets_intermediate_text_without_losing_multi_round_tools():
    """第二轮工具调用须撤回中间文本，并保留两轮工具事件供前端聚合。"""

    class MultiRoundToolAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": "get_weather", "id": "weather-call"}],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="广州晴", tool_call_id="weather-call"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "values",
                {**input_state, "rag_evidence": [{"rank": 1, "evidence_id": "weather#1"}]},
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="我再确认当前月份。", id="draft-1"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": "get_current_month", "id": "month-call"}],
                    ),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="九月", tool_call_id="month-call"),
                    {"langgraph_step": 3},
                ),
            )
            yield (
                "values",
                {
                    **input_state,
                    "rag_evidence": [
                        {"rank": 1, "evidence_id": "weather#1"},
                        {"rank": 2, "evidence_id": "month#1"},
                    ],
                },
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content="建议傍晚慢跑 30 分钟。", id="answer-1"),
                    {"langgraph_step": 3},
                ),
            )

    executor = object.__new__(ReactAgent)
    executor.agent = MultiRoundToolAgent()
    executor.max_steps = 6
    executor.max_tool_calls = 2
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=2),
            ),
        )
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-call", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-call"},
        {"type": "evidence", "items": [{"rank": 1, "evidence_id": "weather#1"}]},
        {"type": "text", "content": "我再确认当前月份。"},
        {"type": "text_reset"},
        {"type": "tool", "id": "month-call", "name": "获取月份"},
        {"type": "tool_completed", "id": "month-call"},
        {"type": "evidence", "items": [{"rank": 2, "evidence_id": "month#1"}]},
        {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
    ]


def test_personalized_agent_emits_evidence_for_each_rag_call():
    """两次 RAG 工具调用必须各自生成新证据事件并保留完整最终证据。"""

    class TwiceRagAgent:
        @staticmethod
        async def astream(input_state, **_kwargs):
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": "rag_summarize", "id": "rag-1"}],
                    ),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="第一条证据", tool_call_id="rag-1"),
                    {"langgraph_step": 1},
                ),
            )
            yield (
                "values",
                {
                    **input_state,
                    "rag_evidence": [{"rank": 1, "evidence_id": "first.md#1"}],
                },
            )
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": "rag_summarize", "id": "rag-2"}],
                    ),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "messages",
                (
                    ToolMessage(content="第二条证据", tool_call_id="rag-2"),
                    {"langgraph_step": 2},
                ),
            )
            yield (
                "values",
                {
                    **input_state,
                    "rag_evidence": [
                        {"rank": 1, "evidence_id": "first.md#1"},
                        {"rank": 1, "evidence_id": "second.md#1"},
                    ],
                },
            )

    executor = object.__new__(ReactAgent)
    executor.agent = TwiceRagAgent()
    executor.max_steps = 5
    executor.max_tool_calls = 4
    result = asyncio.run(
        build_chat_routing_graph(classifier=PersonalizedClassifier()).ainvoke(
            build_initial_chat_state(messages=[{"role": "user", "content": "查两条深蹲资料"}]),
            context=ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(personalized_agent_executor=executor),
            ),
        )
    )

    assert result["events"] == [
        {"type": "tool", "id": "rag-1", "name": "检索知识库"},
        {"type": "tool_completed", "id": "rag-1"},
        {"type": "evidence", "items": [{"rank": 1, "evidence_id": "first.md#1"}]},
        {"type": "tool", "id": "rag-2", "name": "检索知识库"},
        {"type": "tool_completed", "id": "rag-2"},
        {"type": "evidence", "items": [{"rank": 1, "evidence_id": "second.md#1"}]},
    ]
    assert result["rag_evidence"] == [
        {"rank": 1, "evidence_id": "first.md#1"},
        {"rank": 1, "evidence_id": "second.md#1"},
    ]


def test_personalized_graph_rejects_non_json_inner_state():
    """个性化图节点不得把内层 Agent 的非 JSON 短期状态写回外层图。"""

    class InvalidStateInnerAgent:
        @staticmethod
        async def astream_events(_input_state, **_kwargs):
            yield {
                "event": "on_tool_start",
                "run_id": "rag-run",
                "name": "rag_summarize",
                "data": {"input": {}},
            }
            yield {
                "event": "on_tool_end",
                "run_id": "rag-run",
                "name": "rag_summarize",
                "data": {"output": Command(update={"rag_evidence": [{"unsafe": object()}]})},
            }

    executor = object.__new__(ReactAgent)
    executor.agent = InvalidStateInnerAgent()
    executor.max_steps = 5
    executor.max_tool_calls = 2

    with pytest.raises(ValueError, match="个性化 Agent 事件包含不可序列化值"):
        asyncio.run(
            build_chat_routing_graph(classifier=PersonalizedClassifier()).ainvoke(
                build_initial_chat_state(
                    messages=[{"role": "user", "content": "结合我的情况给建议"}]
                ),
                context=ChatRuntimeContext(
                    user_id=5,
                    session_id="session-5",
                    dependencies=SimpleNamespace(personalized_agent_executor=executor),
                ),
            )
        )


def test_monitor_tool_preserves_successful_tool_result():
    """自定义中间件只负责审计与异常隔离，不再承担额度计数。"""
    state = {}
    runtime = ToolRuntime(
        state=state,
        context=ChatRuntimeContext(
            user_id=5,
            session_id="session-5",
            dependencies=SimpleNamespace(),
        ),
        config={},
        stream_writer=lambda _event: None,
        tool_call_id="budget-call",
        store=None,
    )
    calls = []

    def request(call_id):
        return ToolCallRequest(
            tool_call={"name": "example_tool", "args": {}, "id": call_id},
            tool=None,
            state=state,
            runtime=runtime,
        )

    def handler(tool_request):
        calls.append(tool_request.tool_call["id"])
        return ToolMessage(content="ok", tool_call_id=tool_request.tool_call["id"])

    result = monitor_tool.wrap_tool_call(request("call-1"), handler)

    assert result.content == "ok"
    assert calls == ["call-1"]
