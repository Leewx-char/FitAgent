"""官方远程 MCP Gateway 的白名单、隔离和失败路径测试。"""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.core.settings import Settings
from app.services.coros_live_gateway import (
    CorosLiveGateway,
    CorosMcpUnavailableError,
)
from app.services.coros_oauth import CorosAccessCredential


class FakeOAuth:
    """记录每次按用户取凭据的调用，不持有共享 token。"""

    def __init__(self):
        self.calls = []

    def get_access_credential(self, _db, *, user_id, force_refresh=False):
        self.calls.append((user_id, force_refresh))
        return CorosAccessCredential(
            access_token=f"token-{user_id}", issuer="https://mcpcn.coros.com"
        )


class FakeTool:
    """构造带最小 args schema 的异步 LangChain 工具替身。"""

    def __init__(self, name, payload, fields=("startDate", "endDate")):
        self.name = name
        self.args_schema = SimpleNamespace(model_fields={field: object() for field in fields})
        self.payload = payload
        self.calls = []

    async def ainvoke(self, arguments):
        self.calls.append(arguments)
        return SimpleNamespace(content=json.dumps(self.payload))


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
