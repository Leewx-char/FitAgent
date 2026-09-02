"""Agent 工具执行防护栏的无外部依赖测试。"""

from app.services.middleware import _consume_tool_budget, _tool_argument_shape
from app.services.chat_routing_graph import ChatRuntimeContext
from app.services.react_agent import ReactAgent


def test_chat_runtime_context_keeps_request_scoped_dependencies():
    trace = object()
    dependencies = object()

    context = ChatRuntimeContext(
        user_id=7,
        city="广州",
        session_id="session-1",
        trace=trace,
        dependencies=dependencies,
    )

    assert (context.user_id, context.city, context.session_id) == (7, "广州", "session-1")
    assert context.trace is trace
    assert context.dependencies is dependencies


def test_tool_audit_keeps_argument_shape_without_raw_user_value():
    """验证审计记录仅保留工具参数类型而不泄露用户原始值。"""
    shape = _tool_argument_shape({"query": "我的体重是75kg", "city": "广州", "limit": 6})

    assert shape == {"query": "str", "city": "str", "limit": "int"}
    assert "75kg" not in str(shape)
    assert "广州" not in str(shape)


def test_tool_budget_blocks_only_calls_after_limit():
    """验证调用预算在达到上限前放行，超限后的调用被拒绝。"""
    context = {"tool_call_limit": 2}

    assert _consume_tool_budget(context) == (True, 1, 2)
    assert _consume_tool_budget(context) == (True, 2, 2)
    assert _consume_tool_budget(context) == (False, 3, 2)


def test_full_agent_flow_passes_recursion_limit_to_langgraph():
    """验证 Agent 执行流向 LangGraph 传递递归与工具调用限制。"""
    captured = {}

    class FakeGraph:
        @staticmethod
        def stream(_input, **kwargs):
            """记录 LangGraph 流式调用参数并返回空事件序列。"""
            captured.update(kwargs)
            return iter(())

    agent = object.__new__(ReactAgent)
    agent.agent = FakeGraph()
    agent.max_steps = 9
    agent.max_tool_calls = 4

    assert list(agent.execute_stream([{"role": "user", "content": "你好"}])) == []
    assert captured["config"] == {"recursion_limit": 9}
    assert captured["context"]["tool_call_limit"] == 4
