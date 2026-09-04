"""Agent Collector 运行树投影的仓储测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.tools import tool
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langchain_core.tracers.schemas import Run

from app.repositories.agent_trace_repository import AgentTraceRepository
from app.schemas import AgentToolCallResponse


class FakeSession:
    """收集仓储待写入对象，避免测试依赖数据库。"""

    def __init__(self):
        """初始化用于收集待持久化对象的空列表。"""
        self.added = []

    def add(self, value):
        """模拟会话添加单个 ORM 对象。"""
        self.added.append(value)

    def add_all(self, values):
        """模拟会话批量添加 ORM 对象。"""
        self.added.extend(values)


def _save(db, collector, *, request_id="request-2"):
    """以固定请求元数据保存 Collector 运行树。"""
    return AgentTraceRepository.save(
        db, collector, request_id=request_id, session_id="b" * 32, user_id=7,
        user_question="北京天气怎么样？", assistant_answer="北京晴。", status="succeeded",
    )


def test_repository_projects_tool_input_and_output():
    """Collector 工具运行应保存真实输入、输出与问答摘要。"""
    @tool
    def get_weather(city: str) -> dict[str, str]:
        """返回测试城市的天气结果。"""
        return {"city": city, "weather": "晴"}

    collector = RunCollectorCallbackHandler()
    get_weather.invoke({"city": "北京"}, config={"callbacks": [collector]})
    db = FakeSession()
    run = _save(db, collector)

    assert run.user_question == "北京天气怎么样？"
    assert run.assistant_answer == "北京晴。"
    assert db.added[1].tool_input == '{"city": "北京"}'
    assert db.added[1].tool_output == '{"city": "北京", "weather": "晴"}'
    assert db.added[1].status == "succeeded"


def test_repository_marks_failed_tool_and_preserves_error():
    """Collector 异常工具应保存失败状态和错误文本。"""
    @tool
    def get_weather(city: str) -> dict[str, str]:
        """模拟不可用的天气服务。"""
        raise RuntimeError("weather unavailable")

    collector = RunCollectorCallbackHandler()
    with pytest.raises(RuntimeError, match="weather unavailable"):
        get_weather.invoke({"city": "北京"}, config={"callbacks": [collector]})
    db = FakeSession()
    _save(db, collector)

    assert db.added[1].status == "failed"
    assert "weather unavailable" in db.added[1].tool_output


def test_repository_orders_nested_tools_and_handles_empty_collector():
    """嵌套工具按开始时间排序，空 Collector 仍生成零耗时摘要。"""
    start = datetime(2026, 9, 4, tzinfo=UTC)
    early = Run(
        name="early_tool",
        run_type="tool",
        inputs={"order": 1},
        outputs={"result": "first"},
        start_time=start,
        end_time=start + timedelta(milliseconds=10),
    )
    late = Run(
        name="late_tool",
        run_type="tool",
        inputs={"order": 2},
        outputs={"result": "second"},
        start_time=start + timedelta(seconds=1),
        end_time=start + timedelta(seconds=2),
    )
    parent = Run(
        name="agent",
        run_type="chain",
        inputs={},
        start_time=start,
        end_time=start + timedelta(seconds=3),
        child_runs=[late, early],
    )
    collector = RunCollectorCallbackHandler()
    collector.traced_runs.append(parent)
    db = FakeSession()
    run = _save(db, collector)

    assert run.elapsed_ms == 3000
    assert [item.sequence for item in db.added[1:]] == [1, 2]
    assert [item.tool_name for item in db.added[1:]] == ["early_tool", "late_tool"]

    empty_run = _save(FakeSession(), RunCollectorCallbackHandler(), request_id="request-3")
    assert empty_run.elapsed_ms == 0
    assert len(empty_run.id) == 32


def test_tool_response_restores_json_and_keeps_legacy_text():
    """接口响应应还原 JSON，并兼容历史非 JSON 输出。"""
    json_row = AgentToolCallResponse(
        sequence=1,
        tool_name="weather",
        tool_input='{"city": "北京"}',
        tool_output='{"weather": "晴"}',
        status="succeeded",
        elapsed_ms=1,
        created_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    legacy_row = AgentToolCallResponse(
        sequence=2,
        tool_name="weather",
        tool_input="legacy input",
        tool_output="legacy output",
        status="failed",
        elapsed_ms=1,
        created_at=datetime(2026, 9, 4, tzinfo=UTC),
    )

    assert json_row.tool_input == {"city": "北京"}
    assert json_row.tool_output == {"weather": "晴"}
    assert legacy_row.tool_input == "legacy input"
    assert legacy_row.tool_output == "legacy output"
