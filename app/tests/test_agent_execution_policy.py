"""Agent 工具执行防护栏的无外部依赖测试。"""

from app.services.middleware import _consume_tool_budget, _tool_argument_shape
from app.services.react_agent import ReactAgent


def test_tool_audit_keeps_argument_shape_without_raw_user_value():
    shape = _tool_argument_shape({"query": "我的体重是75kg", "city": "广州", "limit": 6})

    assert shape == {"query": "str", "city": "str", "limit": "int"}
    assert "75kg" not in str(shape)
    assert "广州" not in str(shape)


def test_tool_budget_blocks_only_calls_after_limit():
    context = {"tool_call_limit": 2}

    assert _consume_tool_budget(context) == (True, 1, 2)
    assert _consume_tool_budget(context) == (True, 2, 2)
    assert _consume_tool_budget(context) == (False, 3, 2)


def test_full_agent_flow_passes_recursion_limit_to_langgraph():
    captured = {}

    class FakeGraph:
        @staticmethod
        def stream(_input, **kwargs):
            captured.update(kwargs)
            return iter(())

    agent = object.__new__(ReactAgent)
    agent.agent = FakeGraph()
    agent.max_steps = 9
    agent.max_tool_calls = 4

    assert list(agent.execute_stream([{"role": "user", "content": "你好"}])) == []
    assert captured["config"] == {"recursion_limit": 9}
    assert captured["context"]["tool_call_limit"] == 4
