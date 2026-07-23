from datetime import datetime, timedelta


class TestFitness:
    def test_sync_success(self, auth_client, coros_mock):
        """coros 正常返回 2 条 daily_metrics → sync 应 200 + upserted=2"""
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
        assert data["message"] == "同步完成"
        assert data["upserted"] == 2

    def test_sync_coros_failure(self, auth_client, coros_mock):
        """coros 抛异常 → sync 应 502 + message 含失败提示"""
        coros_mock.get_daily_metrics.side_effect = Exception("coros API down")

        resp = auth_client.post("/api/fitness/sync", json={})
        assert resp.status_code == 502
        assert "同步每日指标失败" in resp.json()["message"]

    def test_get_daily_data(self, auth_client, seed_fitness_data):
        """seed 3 条 daily_metrics → GET /daily 应返回 3 条"""
        resp = auth_client.get("/api/fitness/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["data_type"] == "daily_metrics"

    def test_get_activities_empty(self, auth_client):
        """当前用户无 activity 数据 → GET /activities 应返回空列表"""
        resp = auth_client.get("/api/fitness/activities")
        assert resp.status_code == 200
        assert resp.json() == []
