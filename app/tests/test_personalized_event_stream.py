"""个性化 Agent v2 原始事件到 SSE 事件的适配测试。"""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.types import Command

from app.services.chat_routing_graph import (
    ChatRuntimeContext,
    IntentDecision,
    build_chat_routing_graph,
    build_initial_chat_state,
)
from app.services.react_agent import ReactAgent


class EventStreamAgent:
    """按预设顺序产出 LangChain v2 原始事件的测试替身。"""

    def __init__(self, events):
        """保存事件并记录调用参数，便于断言调用协议。"""
        self._events = events
        self.calls = []

    async def astream_events(self, input_state, **kwargs):
        """模拟内层 Agent 的 v2 原始事件流。"""
        self.calls.append({"input_state": input_state, **kwargs})
        for event in self._events:
            yield event


class PersonalizedClassifier:
    """固定路由至个性化 Agent 分支。"""

    def classify(self, _prompt, config=None):
        """返回稳定的个性化路由判断。"""
        del config
        return IntentDecision(route="personalized_agent")


def _event(event, run_id, name, **data):
    """构造测试所需的 LangChain v2 原始事件信封。"""
    return {"event": event, "run_id": run_id, "name": name, "data": data}


def _executor(events):
    """构造只注入事件流替身的轻量个性化执行器。"""
    executor = object.__new__(ReactAgent)
    executor.agent = EventStreamAgent(events)
    executor.max_steps = 8
    executor.max_tool_calls = 4
    return executor


def _run(events):
    """在统一的请求上下文中运行个性化事件适配器。"""
    executor = _executor(events)
    result = asyncio.run(
        executor.astream_personalized_events(
            build_initial_chat_state(messages=[{"role": "user", "content": "广州怎么训练？"}]),
            ChatRuntimeContext(
                user_id=5,
                session_id="session-5",
                dependencies=SimpleNamespace(max_tool_calls=4),
            ),
        )
    )
    return executor, result


def test_personalized_agent_uses_v2_events_and_forwards_text_chunks():
    """正文分块应按原顺序转成 text，且最终消息不重复补发。"""
    agent, result = _run(
        [
            _event(
                "on_chat_model_stream",
                "model-1",
                "ChatDeepSeek",
                chunk=AIMessageChunk(content="建议傍晚"),
            ),
            _event(
                "on_chat_model_stream",
                "model-1",
                "ChatDeepSeek",
                chunk=AIMessageChunk(content="慢跑 30 分钟。"),
            ),
            _event(
                "on_chat_model_end",
                "model-1",
                "ChatDeepSeek",
                output=AIMessage(content="建议傍晚慢跑 30 分钟。"),
            ),
        ]
    )

    assert agent.agent.calls[0]["version"] == "v2"
    assert agent.agent.calls[0]["config"]["recursion_limit"] == 8
    assert result["events"] == [
        {"type": "text", "content": "建议傍晚"},
        {"type": "text", "content": "慢跑 30 分钟。"},
    ]


def test_personalized_agent_uses_tool_run_ids_and_waits_for_parallel_tools():
    """并行同名工具以运行 ID 区分，全部完成前不转发中间文本。"""
    _, result = _run(
        [
            _event("on_tool_start", "weather-run-1", "get_weather", input={}),
            _event("on_tool_start", "weather-run-2", "get_weather", input={}),
            _event("on_tool_end", "weather-run-2", "get_weather", output="广州晴"),
            _event(
                "on_chat_model_stream",
                "model-1",
                "ChatDeepSeek",
                chunk=AIMessageChunk(content="不应提前显示。"),
            ),
            _event("on_tool_end", "weather-run-1", "get_weather", output="广州晴"),
            _event(
                "on_chat_model_stream",
                "model-2",
                "ChatDeepSeek",
                chunk=AIMessageChunk(content="建议清晨进行轻松跑。"),
            ),
        ]
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-run-1", "name": "查询天气"},
        {"type": "tool", "id": "weather-run-2", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-run-2"},
        {"type": "tool_completed", "id": "weather-run-1"},
        {"type": "text", "content": "建议清晨进行轻松跑。"},
    ]


def test_personalized_agent_falls_back_to_final_ai_message_after_empty_chunks():
    """模型没有正文分块时，非工具最终 AIMessage 必须作为正式回答发送。"""
    _, result = _run(
        [
            _event("on_tool_start", "weather-run", "get_weather", input={}),
            _event("on_tool_end", "weather-run", "get_weather", output="广州晴"),
            _event(
                "on_chat_model_end",
                "model-2",
                "ChatDeepSeek",
                output=AIMessage(content="建议傍晚慢跑 30 分钟。"),
            ),
        ]
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-run", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-run"},
        {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
    ]


def test_personalized_agent_completes_tool_error_before_fallback_text():
    """工具异常也要结束对应工具链，避免吞掉后续模型降级回答。"""
    _, result = _run(
        [
            _event("on_tool_start", "weather-run", "get_weather", input={}),
            _event(
                "on_tool_error",
                "weather-run",
                "get_weather",
                error=RuntimeError("weather provider unavailable"),
            ),
            _event(
                "on_chat_model_end",
                "model-2",
                "ChatDeepSeek",
                output=AIMessage(content="天气服务暂不可用，建议先做室内力量训练。"),
            ),
        ]
    )

    assert result["events"] == [
        {"type": "tool", "id": "weather-run", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-run"},
        {"type": "text", "content": "天气服务暂不可用，建议先做室内力量训练。"},
    ]


def test_personalized_agent_emits_rag_evidence_once_per_tool_end():
    """两次检索分别保留证据；同一工具结束事件不能重复展示。"""
    first = {"rank": 1, "evidence_id": "first.md#1"}
    second = {"rank": 1, "evidence_id": "second.md#1"}
    _, result = _run(
        [
            _event("on_tool_start", "rag-run-1", "rag_summarize", input={}),
            _event(
                "on_tool_end",
                "rag-run-1",
                "rag_summarize",
                output=Command(update={"rag_evidence": [first]}),
            ),
            _event(
                "on_tool_end",
                "rag-run-1",
                "rag_summarize",
                output=Command(update={"rag_evidence": [first]}),
            ),
            _event("on_tool_start", "rag-run-2", "rag_summarize", input={}),
            _event(
                "on_tool_end",
                "rag-run-2",
                "rag_summarize",
                output=Command(update={"rag_evidence": [second]}),
            ),
        ]
    )

    assert result["events"] == [
        {"type": "tool", "id": "rag-run-1", "name": "检索知识库"},
        {"type": "tool_completed", "id": "rag-run-1"},
        {"type": "evidence", "items": [first]},
        {"type": "tool", "id": "rag-run-2", "name": "检索知识库"},
        {"type": "tool_completed", "id": "rag-run-2"},
        {"type": "evidence", "items": [second]},
    ]
    assert result["rag_evidence"] == [first, second]


def test_personalized_agent_resets_temporary_text_before_tool_start():
    """工具前的临时说明要撤回，持久化事件仅保留工具后的正文。"""
    _, result = _run(
        [
            _event(
                "on_chat_model_stream",
                "model-1",
                "ChatDeepSeek",
                chunk=AIMessageChunk(content="我先查一下天气。"),
            ),
            _event("on_tool_start", "weather-run", "get_weather", input={}),
            _event("on_tool_end", "weather-run", "get_weather", output="广州晴"),
            _event(
                "on_chat_model_stream",
                "model-2",
                "ChatDeepSeek",
                chunk=AIMessageChunk(content="建议傍晚慢跑 30 分钟。"),
            ),
        ]
    )

    assert result["events"] == [
        {"type": "text", "content": "我先查一下天气。"},
        {"type": "text_reset"},
        {"type": "tool", "id": "weather-run", "name": "查询天气"},
        {"type": "tool_completed", "id": "weather-run"},
        {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
    ]


def test_personalized_graph_rejects_non_json_rag_evidence_from_tool_event():
    """工具事件携带不可序列化证据时，外层图仍应保持原有失败语义。"""
    executor = _executor(
        [
            _event("on_tool_start", "rag-run", "rag_summarize", input={}),
            _event(
                "on_tool_end",
                "rag-run",
                "rag_summarize",
                output=Command(update={"rag_evidence": [{"unsafe": object()}]}),
            ),
        ]
    )

    with pytest.raises(ValueError, match="个性化 Agent 事件包含不可序列化值"):
        asyncio.run(
            build_chat_routing_graph(classifier=PersonalizedClassifier()).ainvoke(
                build_initial_chat_state(messages=[{"role": "user", "content": "深蹲怎么做？"}]),
                context=ChatRuntimeContext(
                    user_id=5,
                    session_id="session-5",
                    dependencies=SimpleNamespace(personalized_agent_executor=executor),
                ),
            )
        )
