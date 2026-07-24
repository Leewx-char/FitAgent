"""Agent 执行轨迹模型与仓储测试。"""

from app.repositories.agent_trace_repository import AgentTraceRepository
from app.services.agent_trace import AgentTrace


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added.extend(values)


def test_agent_trace_keeps_only_safe_tool_metadata():
    trace = AgentTrace(request_id="request-1")
    trace.record_tool(
        tool_name="rag_summarize",
        argument_shape={"query": "str", "source": "str"},
        status="success",
        elapsed_ms=123,
    )
    trace.finish("succeeded")

    assert trace.status == "succeeded"
    assert trace.tool_calls[0].argument_shape == {"query": "str", "source": "str"}
    assert "用户原文" not in str(trace.tool_calls)


def test_trace_repository_saves_run_and_ordered_tool_calls():
    trace = AgentTrace(run_id="a" * 32, request_id="request-2")
    trace.record_tool(
        tool_name="get_weather",
        argument_shape={"city": "str"},
        status="success",
        elapsed_ms=88,
    )
    trace.finish("succeeded")
    db = FakeSession()

    run = AgentTraceRepository.save(db, trace, session_id="b" * 32, user_id=7)

    assert run.tool_call_count == 1
    assert run.session_id == "b" * 32
    assert len(db.added) == 2
    assert db.added[1].sequence == 1
    assert db.added[1].argument_shape == '{"city": "str"}'
