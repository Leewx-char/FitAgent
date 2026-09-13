"""实时 COROS Dashboard 接口契约。"""

from datetime import date, timedelta

from app.services.coros_live_gateway import CorosMcpUnavailableError, LiveFitnessData
from app.services.coros_oauth import CorosNotConnectedError


class FakeGateway:
    """仅返回内存快照，验证路由没有写入运动数据表。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def fetch_snapshot(self, db, *, user_id, start_date, end_date):
        self.calls.append((user_id, start_date, end_date))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_snapshot_returns_realtime_data_without_fitness_table(auth_client, monkeypatch):
    """Dashboard 仅消费 Gateway 返回的内存记录，模型元数据中不再有 fitness_data。"""

    from app.api.routers import fitness
    from app.core.database import Base

    today = date.today()
    gateway = FakeGateway(
        LiveFitnessData(
            start_date=today - timedelta(weeks=4),
            end_date=today,
            daily_metrics=[{"date": today.isoformat(), "rhr": 55, "training_load": 42}],
            sleep_records=[],
            activities=[{"external_id": "run-1", "date": today.isoformat(), "name": "Run"}],
            unavailable_sources=["sleep"],
        )
    )
    monkeypatch.setattr(fitness, "get_coros_live_gateway", lambda: gateway)

    response = auth_client.get("/api/fitness/snapshot?weeks=4")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["partial"] is True
    assert payload["unavailable_sources"] == ["sleep"]
    assert payload["activities"][0]["external_id"] == "run-1"
    assert len(gateway.calls) == 1
    assert "fitness_data" not in Base.metadata.tables


def test_snapshot_week_one_is_exactly_seven_inclusive_calendar_days(auth_client, monkeypatch):
    """weeks=1 代表包含今天在内的七个自然日，避免原先多取一天。"""

    from app.api.routers import fitness

    today = date.today()
    gateway = FakeGateway(LiveFitnessData(start_date=today, end_date=today))
    monkeypatch.setattr(fitness, "get_coros_live_gateway", lambda: gateway)

    response = auth_client.get("/api/fitness/snapshot?weeks=1")

    assert response.status_code == 200
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1:] == (today - timedelta(days=6), today)


def test_snapshot_requires_connection(auth_client, monkeypatch):
    """未连接用户不会误报为上游故障。"""

    from app.api.routers import fitness

    monkeypatch.setattr(
        fitness,
        "get_coros_live_gateway",
        lambda: FakeGateway(CorosNotConnectedError("not connected")),
    )

    response = auth_client.get("/api/fitness/snapshot")

    assert response.status_code == 409
    assert "尚未连接" in response.json()["messages"][0]


def test_snapshot_maps_all_source_failure_to_502(auth_client, monkeypatch):
    """三源都失败时不返回伪造的空成功快照。"""

    from app.api.routers import fitness

    monkeypatch.setattr(
        fitness,
        "get_coros_live_gateway",
        lambda: FakeGateway(CorosMcpUnavailableError("unavailable")),
    )

    response = auth_client.get("/api/fitness/snapshot")

    assert response.status_code == 502
    assert "实时数据暂不可用" in response.json()["messages"][0]
