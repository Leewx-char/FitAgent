"""Agent 工具执行防护栏的无外部依赖测试。"""

import asyncio
from types import SimpleNamespace

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from app.services.middleware import _tool_argument_shape
from app.services.chat_routing_graph import ChatRuntimeContext
from app.services.react_agent import PersonalizedAgentState, ReactAgent
from app.services.middleware import monitor_tool


class ToolBindingFakeModel(FakeMessagesListChatModel):
    """支持工具绑定的固定消息模型。"""

    def bind_tools(self, *_args, **_kwargs):
        """返回自身，以便 create_agent 执行预设工具调用。"""
        return self


def _invoke_parallel_tool_calls(tool_names: list[str], tool_limit: int):
    """以真实 create_agent 执行同一 AIMessage 中的多个工具调用。"""
    calls = []

    @tool
    def first():
        """记录第一个测试工具调用。"""
        calls.append("first")
        return "first"

    @tool
    def second():
        """记录第二个测试工具调用。"""
        calls.append("second")
        return "second"

    @tool
    def third():
        """记录第三个测试工具调用。"""
        calls.append("third")
        return "third"

    tools = {"first": first, "second": second, "third": third}
    tool_calls = [
        {"name": name, "args": {}, "id": f"call-{index}"}
        for index, name in enumerate(tool_names, start=1)
    ]
    agent = create_agent(
        model=ToolBindingFakeModel(
            responses=[AIMessage(content="", tool_calls=tool_calls), AIMessage(content="完成")]
        ),
        tools=[tools[name] for name in tool_names],
        middleware=[
            ToolCallLimitMiddleware(run_limit=tool_limit, exit_behavior="continue"),
            monitor_tool,
        ],
        state_schema=PersonalizedAgentState,
        context_schema=ChatRuntimeContext,
    )
    result = agent.invoke(
        {
            "messages": [{"role": "user", "content": "执行工具"}],
            "retrieval_history": [],
            "rag_evidence": [],
            "report": False,
        },
        context=ChatRuntimeContext(
            user_id=1,
            session_id="parallel-tools",
            dependencies=SimpleNamespace(max_tool_calls=tool_limit),
        ),
    )
    return result, calls


def test_chat_runtime_context_keeps_request_scoped_dependencies():
    """请求上下文应只保存身份字段和可替换执行依赖。"""
    dependencies = object()

    context = ChatRuntimeContext(
        user_id=7,
        session_id="session-1",
        dependencies=dependencies,
    )

    assert (context.user_id, context.session_id) == (7, "session-1")
    assert not hasattr(context, "city")
    assert context.dependencies is dependencies


def test_tool_audit_keeps_argument_shape_without_raw_user_value():
    """验证审计记录仅保留工具参数类型而不泄露用户原始值。"""
    shape = _tool_argument_shape({"query": "我的体重是75kg", "city": "广州", "limit": 6})

    assert shape == {"query": "str", "city": "str", "limit": "int"}
    assert "75kg" not in str(shape)
    assert "广州" not in str(shape)


def test_create_agent_allows_two_parallel_tool_calls_without_count_conflict():
    """同一 AIMessage 的两个工具调用应分别计数且不会触发并发状态冲突。"""
    result, calls = _invoke_parallel_tool_calls(["first", "second"], tool_limit=2)

    assert set(calls) == {"first", "second"}
    assert "tool_call_count" not in result
    assert all(
        message.status != "error"
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    )


def test_create_agent_rejects_parallel_tool_calls_after_configured_limit():
    """同一 AIMessage 的第三个工具调用应在并行批次中被预算拦截。"""
    result, calls = _invoke_parallel_tool_calls(["first", "second", "third"], tool_limit=2)

    assert set(calls) == {"first", "second"}
    assert "tool_call_count" not in result
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    limit_messages = [message for message in tool_messages if message.status == "error"]
    assert len(limit_messages) == 1
    assert limit_messages[0].tool_call_id == "call-3"
    assert "Tool call limit exceeded" in str(limit_messages[0].content)


def test_execute_stream_passes_request_context_to_routing_graph():
    """公开入口应向外层路由图传递请求级上下文与值流模式。"""
    captured = {}

    class FakeGraph:
        @staticmethod
        async def astream(_input, **kwargs):
            """记录 LangGraph 流式调用参数并返回空事件序列。"""
            captured.update(kwargs)
            if False:
                yield None

    agent = object.__new__(ReactAgent)
    agent.direct_rag_executor = object()
    agent.routing_graph = FakeGraph()
    agent.max_steps = 9
    agent.max_tool_calls = 4

    assert (
        asyncio.run(
            _collect(
                agent.execute_stream(
                    [{"role": "user", "content": "你好"}], user_id=7, session_id="session-7"
                )
            )
        )
        == []
    )
    assert captured["stream_mode"] == ["custom", "values"]
    assert captured["context"].user_id == 7
    assert captured["context"].session_id == "session-7"
    assert captured["context"].dependencies.max_tool_calls == 4


async def _collect(stream):
    """收集异步公开流中的所有事件。"""

    return [item async for item in stream]
