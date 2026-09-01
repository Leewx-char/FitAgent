from datetime import datetime, timedelta


class TestFitness:
    def test_sync_succeeds_when_sleep_data_is_empty(self, auth_client, coros_mock):
        """未佩戴手表导致睡眠为空是正常结果，不能把同步标记为失败或部分失败。"""
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        coros_mock.get_daily_metrics.return_value = [
            {"date": yesterday, "training_load": 50, "rhr": 55},
            {"date": today, "training_load": 55, "rhr": 56},
        ]
        coros_mock.get_sleep_data.return_value = []
        coros_mock.list_activities.return_value = {"activities": []}

        resp = auth_client.post("/api/fitness/sync", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == ["同步完成"]
        assert data["data"]["upserted"] == 2
        assert data["data"]["partial"] is False
        assert data["data"]["unavailable_sources"] == []
        coros_mock.sync_cache.assert_called_once()

    def test_sync_coros_failure(self, auth_client, coros_mock):
        """coros 抛异常 → sync 应 502 + message 含失败提示"""
        coros_mock.get_daily_metrics.side_effect = Exception("coros API down")

        resp = auth_client.post("/api/fitness/sync", json={})
        assert resp.status_code == 502
        assert "未返回可写入" in resp.json()["messages"][0]

    def test_sync_cache_failure_returns_502(self, auth_client, coros_mock):
        """验证 Coros 缓存同步失败会返回可识别的 502 错误。"""
        coros_mock.sync_cache.side_effect = RuntimeError("provider unavailable")

        resp = auth_client.post("/api/fitness/sync", json={})

        assert resp.status_code == 502
        assert "同步 Coros 缓存失败" in resp.json()["messages"][0]

    def test_sync_persists_available_sources_when_sleep_is_unavailable(
        self, auth_client, coros_mock
    ):
        """验证睡眠源不可用时仍持久化其他可用运动数据。"""
        today = datetime.now().strftime("%Y%m%d")
        coros_mock.sync_cache.return_value = {
            "partial": True,
            "failed_sources": ["sleep"],
            "cached_source_counts": {"daily": 1, "sleep": 0, "activities": 0},
        }
        coros_mock.get_daily_metrics.return_value = [{"date": today, "training_load": 42}]
        coros_mock.list_activities.return_value = {"activities": []}

        response = auth_client.post("/api/fitness/sync", json={})

        assert response.status_code == 200
        assert response.json()["data"]["partial"] is True
        assert response.json()["data"]["unavailable_sources"] == ["sleep"]
        assert len(auth_client.get("/api/fitness/daily").json()["data"]) == 1

    def test_get_daily_data(self, auth_client, seed_fitness_data):
        """seed 3 条 daily_metrics → GET /daily 应返回 3 条"""
        resp = auth_client.get("/api/fitness/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 3
        assert data["data"][0]["data_type"] == "daily_metrics"

    def test_get_activities_empty(self, auth_client):
        """当前用户无 activity 数据 → GET /activities 应返回空列表"""
        resp = auth_client.get("/api/fitness/activities")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_sync_keeps_multiple_activities_on_the_same_day(self, auth_client, coros_mock):
        """活动的幂等键应来自上游 activity id，而不是日期。"""
        today = datetime.now().strftime("%Y%m%d")
        coros_mock.get_daily_metrics.return_value = []
        coros_mock.get_sleep_data.return_value = []
        coros_mock.list_activities.return_value = {
            "activities": [
                {"id": "morning-run", "start_time": f"{today}T070000", "name": "Run"},
                {"id": "evening-run", "start_time": f"{today}T190000", "name": "Run"},
            ]
        }

        response = auth_client.post("/api/fitness/sync", json={})

        assert response.status_code == 200
        activities = auth_client.get("/api/fitness/activities").json()["data"]
        assert len(activities) == 2
