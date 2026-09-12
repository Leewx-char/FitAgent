"""实时快照的受限聚合和 Agent 读取边界。"""

from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace

from langchain.tools import ToolRuntime

from app.services.chat_routing_graph import ChatRuntimeContext
from app.services.coros_live_gateway import LiveFitnessData
from app.services.fitness_insights import (
    build_activity_snapshot,
    build_fitness_snapshot,
    list_activity_candidates,
)


def _runtime(user_id: int | None) -> ToolRuntime:
    return ToolRuntime(
        state={},
        context=ChatRuntimeContext(
            user_id=user_id or 0,
            session_id="fitness-summary-test",
            dependencies=SimpleNamespace(),
        ),
        config={},
        stream_writer=lambda _event: None,
        tool_call_id="fitness-summary-test",
        store=None,
    )


def test_build_snapshot_and_candidates_are_pure_memory_operations():
    """汇总服务只接收 dict 列表，不依赖 ORM 或原始载荷。"""

    today = date.today()
    snapshot = build_fitness_snapshot(
        daily_records=[{"date": today.isoformat(), "rhr": 52, "avg_sleep_hrv": 70}],
        sleep_records=[
            {
                "date": today.isoformat(),
                "total_duration_minutes": 450,
                "phases": {"deep_minutes": 100},
            }
        ],
        activities=[
            {
                "external_id": "morning-run",
                "date": today.isoformat(),
                "name": "跑步",
                "duration_seconds": 1800,
            },
            {
                "external_id": "evening-run",
                "date": today.isoformat(),
                "name": "跑步",
                "duration_seconds": 2400,
            },
        ],
    )

    candidates = list_activity_candidates(
        [
            {"external_id": "morning-run", "date": today.isoformat(), "name": "跑步"},
            {"external_id": "outside", "date": (today - timedelta(days=1)).isoformat()},
        ],
        activity_date=today,
    )

    assert snapshot.activity_count == 2
    assert "睡眠时长" in snapshot.to_prompt()
    assert [candidate.external_id for candidate in candidates] == ["morning-run"]
    assert build_activity_snapshot({"name": "缺少 id"}) is None


def test_agent_uses_live_gateway_and_never_needs_persistent_activity(monkeypatch):
    """同日活动候选和详情均来自当次 MCP 请求。"""

    from app.services import agent_tools

    activity_day = date.today() - timedelta(days=1)

    class Gateway:
        def fetch_snapshot(self, _db, **_kwargs):
            return LiveFitnessData(
                start_date=activity_day,
                end_date=activity_day,
                activities=[
                    {
                        "external_id": "evening-run",
                        "date": activity_day.isoformat(),
                        "start_time": f"{activity_day.isoformat()}T19:00:00",
                        "name": "夜跑",
                        "duration_seconds": 2400,
                    }
                ],
            )

        def fetch_activity_detail(self, _db, **_kwargs):
            return {
                "external_id": "evening-run",
                "start_time": f"{activity_day.isoformat()}T19:00:00",
                "name": "夜跑",
                "duration_seconds": 2400,
                "distance_meters": 6000,
            }

    @contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr(agent_tools, "get_coros_live_gateway", lambda: Gateway())
    monkeypatch.setattr(agent_tools, "get_db_session", fake_session)
    compact_day = activity_day.strftime("%Y%m%d")

    candidates = agent_tools.get_fitness_summary.func(
        runtime=_runtime(1), start_day=compact_day, end_day=compact_day
    )
    detail = agent_tools.get_fitness_summary.func(
        runtime=_runtime(1),
        start_day=compact_day,
        end_day=compact_day,
        activity_id="evening-run",
    )

    assert "evening-run" in candidates
    assert "单次活动摘要" in detail
    assert "6.00公里" in detail
