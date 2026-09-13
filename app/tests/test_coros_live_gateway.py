"""官方远程 MCP Gateway 的白名单、隔离和失败路径测试。"""

import json
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.settings import Settings
from app.services.coros_live_gateway import (
    CorosLiveGateway,
    CorosMcpUnavailableError,
)
from app.services.coros_oauth import CorosAccessCredential, CorosReconnectionRequiredError


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "coros_mcp"


def fixture_text(name: str) -> str:
    """读取脱敏的 COROS MCP 文本样本，避免在测试中内嵌原始数据。"""

    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


class FakeOAuth:
    """记录每次按用户取凭据的调用，不持有共享 token。"""

    def __init__(self):
        self.calls = []
        self.reconnect_users = []

    def get_access_credential(self, _db, *, user_id, force_refresh=False):
        self.calls.append((user_id, force_refresh))
        return CorosAccessCredential(
            access_token=f"token-{user_id}", issuer="https://mcpcn.coros.com"
        )

    def mark_reconnection_required(self, _db, *, user_id):
        self.reconnect_users.append(user_id)


class FakeTool:
    """构造带最小 args schema 的异步 LangChain 工具替身。"""

    def __init__(self, name, payload, fields=("startDate", "endDate")):
        self.name = name
        self.args_schema = SimpleNamespace(model_fields={field: object() for field in fields})
        self.payload = payload
        self.calls = []

    async def ainvoke(self, arguments):
        self.calls.append(arguments)
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(content=content)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeClient:
    def __init__(self, config):
        self.config = config

    def session(self, _name):
        return FakeSession()


@pytest.mark.parametrize("user_id", [7, 8])
def test_gateway_calls_only_allowlisted_read_tools_and_keeps_user_token_isolated(user_id):
    """tools/list 中即使有额外工具，Gateway 也只调用三个固定读取工具。"""

    today = date.today().isoformat()
    activities = FakeTool(
        "querySportRecords",
        {"records": [{"id": "run-1", "startTime": today, "name": "Run"}]},
    )
    daily = FakeTool("queryDailyHealthData", {"records": [{"date": today, "rhr": 55}]})
    sleep = FakeTool("querySleepData", {"records": [{"date": today, "totalSleepMinutes": 420}]})
    forbidden = FakeTool("updateTrainingPlan", {"records": []})
    oauth = FakeOAuth()

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep, forbidden]

    gateway = CorosLiveGateway(
        oauth_service=oauth,
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )

    snapshot = gateway.fetch_snapshot(
        object(), user_id=user_id, start_date=date.today(), end_date=date.today()
    )

    assert snapshot.activities[0]["external_id"] == "run-1"
    assert len(activities.calls) == len(daily.calls) == len(sleep.calls) == 1
    assert forbidden.calls == []
    assert oauth.calls == [(user_id, False)]


def test_gateway_returns_partial_when_one_source_fails():
    """一个上游工具失败不污染其余成功数据。"""

    class FailingTool(FakeTool):
        async def ainvoke(self, arguments):
            del arguments
            raise RuntimeError("provider failure")

    today = date.today().isoformat()
    activities = FakeTool("querySportRecords", {"records": []})
    daily = FakeTool("queryDailyHealthData", {"records": [{"date": today, "rhr": 55}]})
    sleep = FailingTool("querySleepData", {})

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )
    snapshot = gateway.fetch_snapshot(
        object(), user_id=1, start_date=date.today(), end_date=date.today()
    )

    assert snapshot.daily_metrics
    assert snapshot.unavailable_sources == ["sleep"]


def test_gateway_reads_daily_with_schema_declared_seven_day_limit():
    """daily schema 声明 days 时，网关按请求范围传递受控天数。"""

    today = date.today()
    activities = FakeTool("querySportRecords", {"records": []})
    daily = FakeTool(
        "queryDailyHealthData",
        {
            "records": [
                {"date": today.isoformat(), "rhr": 55},
                {"date": "2020-01-01", "rhr": 60},
            ]
        },
        fields=("days",),
    )
    sleep = FakeTool("querySleepData", {"records": []})

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )

    snapshot = gateway.fetch_snapshot(
        object(), user_id=1, start_date=today, end_date=today
    )

    assert daily.calls == [{"days": 1}]
    assert snapshot.daily_metrics == [
        {
            "date": today.isoformat(),
            "rhr": 55,
            "avg_sleep_hrv": None,
            "training_load": None,
            "training_load_ratio": None,
            "tired_rate": None,
            "vo2max": None,
        }
    ]
    expected_bounded_call = {
        "startDate": today.strftime("%Y%m%d"),
        "endDate": today.strftime("%Y%m%d"),
    }
    assert activities.calls == sleep.calls == [expected_bounded_call]


def test_gateway_parses_coros_text_and_uses_compact_complete_filters():
    """文本工具结果会映射成安全快照，并按实际 schema 提供完整筛选参数。"""

    start_date = date(2026, 9, 7)
    end_date = date(2026, 9, 13)
    activities = FakeTool(
        "querySportRecords",
        fixture_text("sport_records.txt"),
        fields=(
            "startDate",
            "endDate",
            "sportTypeCodes",
            "minDistanceKm",
            "maxDistanceKm",
            "minDurationMinutes",
            "maxDurationMinutes",
            "maxAveragePace",
            "locationKeyword",
            "limit",
        ),
    )
    daily = FakeTool("queryDailyHealthData", fixture_text("daily_health.txt"), fields=("days",))
    sleep = FakeTool(
        "querySleepData", fixture_text("sleep.txt"), fields=("startDate", "endDate", "days")
    )
    load = FakeTool(
        "queryTrainingLoadAssessment", fixture_text("training_load.txt"), fields=("days",)
    )
    rhr = FakeTool(
        "queryRestingHeartRate", "Resting Heart Rate — Last 7 days\nNo data", fields=("days",)
    )
    hrv = FakeTool(
        "querySleepHrv",
        fixture_text("no_data.txt"),
        fields=("startDate", "endDate", "days"),
    )

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep, load, rhr, hrv]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )

    snapshot = gateway.fetch_snapshot(
        object(), user_id=1, start_date=start_date, end_date=end_date
    )

    assert activities.calls == [
        {
            "startDate": "20260907",
            "endDate": "20260913",
            "sportTypeCodes": [65535],
            "minDistanceKm": 0,
            "maxDistanceKm": 10000,
            "minDurationMinutes": 0,
            "maxDurationMinutes": 1440,
            "maxAveragePace": "",
            "locationKeyword": "",
            "limit": 100,
        }
    ]
    assert daily.calls == [{"days": 7}]
    assert sleep.calls == [{"startDate": "20260907", "endDate": "20260913", "days": 7}]
    assert load.calls == rhr.calls == [{"days": 7}]
    assert hrv.calls == [{"startDate": "20260907", "endDate": "20260913", "days": 7}]
    assert snapshot.activities == [
        {
            "external_id": "activity-redacted",
            "date": "2026-09-11",
            "start_time": "",
            "name": "Track Run",
            "sport_name": "Track Run",
            "duration_seconds": 1257,
            "distance_meters": 3000,
            "avg_heart_rate": 151,
            "max_heart_rate": None,
            "training_load": None,
            "calories": 273,
        }
    ]
    assert all(
        "location" not in record and "coordinates" not in record
        for record in snapshot.activities
    )
    assert snapshot.daily_metrics[0]["steps"] == 7039
    assert snapshot.daily_metrics[1]["calories"] == 273
    assert snapshot.daily_metrics[1]["exercise_minutes"] == 21
    assert snapshot.daily_metrics[1]["training_load"] == 11
    assert snapshot.daily_metrics[1]["training_load_ratio"] == 0.25
    assert snapshot.sleep_records == [
        {
            "date": "2026-09-11",
            "total_duration_minutes": None,
            "phases": {
                "awake_minutes": 0,
                "rem_minutes": 0,
                "light_minutes": 0,
                "deep_minutes": 0,
            },
            "nap_minutes": 0,
        }
    ]
    assert snapshot.unavailable_sources == []


def test_gateway_treats_recognized_no_data_as_empty_and_text_errors_as_unavailable(caplog):
    """无数据文本不伪造数值；工具异常文本只产生脱敏的来源失败日志。"""

    activities = FakeTool("querySportRecords", {"records": []})
    daily = FakeTool("queryDailyHealthData", {"records": []})
    sleep = FakeTool("querySleepData", fixture_text("tool_error.txt"))

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )
    with caplog.at_level(logging.WARNING, logger="coros_live"):
        snapshot = gateway.fetch_snapshot(
            object(), user_id=1, start_date=date.today(), end_date=date.today()
        )

    assert snapshot.sleep_records == []
    assert snapshot.unavailable_sources == ["sleep"]
    assert "COROS_MCP_SOURCE_TEXT_FAILED source=sleep" in caplog.text
    assert "session context pollution" not in caplog.text


def test_gateway_logs_sanitized_daily_source_failure(caplog):
    """日报工具失败时记录可诊断且不泄露上游异常正文的日志。"""

    class FailingDailyTool(FakeTool):
        async def ainvoke(self, arguments):
            del arguments
            raise RuntimeError("provider failure token=secret-value")

    activities = FakeTool("querySportRecords", {"records": []})
    daily = FailingDailyTool("queryDailyHealthData", {})
    sleep = FakeTool("querySleepData", {"records": []})

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )
    with caplog.at_level(logging.WARNING, logger="app.services.coros_live_gateway"):
        snapshot = gateway.fetch_snapshot(
            object(), user_id=1, start_date=date.today(), end_date=date.today()
        )

    assert snapshot.unavailable_sources == ["daily"]
    assert (
        "COROS_MCP_TOOL_CALL_FAILED tool=queryDailyHealthData error_type=RuntimeError"
        in caplog.text
    )
    assert "COROS_MCP_SOURCE_UNAVAILABLE source=daily error_type=CorosMcpError" in caplog.text
    assert "secret-value" not in caplog.text


def test_gateway_fails_when_all_sources_fail():
    """三个目标源均失败时必须显式失败，不能伪造空成功。"""

    class FailingTool(FakeTool):
        async def ainvoke(self, arguments):
            del arguments
            raise RuntimeError("provider failure")

    async def load_tools(_session, **_kwargs):
        return [
            FailingTool("querySportRecords", {}),
            FailingTool("queryDailyHealthData", {}),
            FailingTool("querySleepData", {}),
        ]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )

    with pytest.raises(CorosMcpUnavailableError):
        gateway.fetch_snapshot(object(), user_id=1, start_date=date.today(), end_date=date.today())


def test_gateway_reads_bounded_numbered_pages_before_building_snapshot():
    """上游显式标记 hasMore 时按受控 page 参数继续读取，不能静默丢失活动。"""

    class PagedActivities(FakeTool):
        async def ainvoke(self, arguments):
            self.calls.append(arguments.copy())
            activity_id = f"run-{arguments['page']}"
            payload = {
                "records": [{"id": activity_id, "startTime": date.today().isoformat()}],
                "hasMore": arguments["page"] == 1,
            }
            return SimpleNamespace(content=json.dumps(payload))

    activities = PagedActivities(
        "querySportRecords",
        {},
        fields=("startDate", "endDate", "page", "pageSize"),
    )
    daily = FakeTool("queryDailyHealthData", {"records": []})
    sleep = FakeTool("querySleepData", {"records": []})

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )
    snapshot = gateway.fetch_snapshot(
        object(), user_id=1, start_date=date.today(), end_date=date.today()
    )

    assert [record["external_id"] for record in snapshot.activities] == ["run-1", "run-2"]
    assert [call["page"] for call in activities.calls] == [1, 2]
    assert all(call["pageSize"] == 100 for call in activities.calls)


def test_gateway_requires_same_day_candidate_before_activity_detail():
    """Gateway 自身拒绝未经实时活动候选验证的详情读取。"""

    today = date.today()
    activities = FakeTool(
        "querySportRecords",
        {"records": [{"id": "run-1", "startTime": today.isoformat()}]},
    )
    daily = FakeTool("queryDailyHealthData", {"records": []})
    sleep = FakeTool("querySleepData", {"records": []})
    details = FakeTool(
        "getActivityDetail",
        {"id": "run-1", "startTime": today.isoformat(), "distanceMeters": 5000},
        fields=("activityId",),
    )

    async def load_tools(_session, **_kwargs):
        return [activities, daily, sleep, details]

    gateway = CorosLiveGateway(
        oauth_service=FakeOAuth(),
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )

    assert (
        gateway.fetch_activity_detail(
            object(), user_id=1, activity_id="not-selected", activity_date=today
        )
        is None
    )
    assert details.calls == []

    detail = gateway.fetch_activity_detail(
        object(), user_id=1, activity_id="run-1", activity_date=today
    )

    assert detail is not None
    assert detail["external_id"] == "run-1"
    assert details.calls == [{"activityId": "run-1"}]


def test_gateway_marks_connection_reconnect_required_after_second_401():
    """MCP 两次拒绝 token 后只刷新一次，并持久化重新连接信号。"""

    class UnauthorizedError(RuntimeError):
        status_code = 401

    class UnauthorizedTool(FakeTool):
        async def ainvoke(self, arguments):
            del arguments
            raise UnauthorizedError()

    async def load_tools(_session, **_kwargs):
        return [
            UnauthorizedTool("querySportRecords", {}),
            FakeTool("queryDailyHealthData", {"records": []}),
            FakeTool("querySleepData", {"records": []}),
        ]

    oauth = FakeOAuth()
    gateway = CorosLiveGateway(
        oauth_service=oauth,
        settings=Settings(coros_mcp_timeout_seconds=5),
        client_factory=FakeClient,
        tool_loader=load_tools,
    )

    with pytest.raises(CorosReconnectionRequiredError, match="授权已失效"):
        gateway.fetch_snapshot(object(), user_id=3, start_date=date.today(), end_date=date.today())

    assert oauth.calls == [(3, False), (3, True)]
    assert oauth.reconnect_users == [3]
