"""用户可控长期记忆生命周期的集成测试。"""


class TestMemory:
    def test_chat_creates_proposals_but_not_confirmed_memories(
        self, auth_client, agent_mock, memory_backend
    ):
        """验证聊天只产生待确认记忆，确认后才变为 confirmed 状态。"""
        memory_backend.extracted = ["city: 成都", "training_goal: 减脂", "injuries: 膝盖不适"]
        response = auth_client.post(
            "/api/chat",
            json={"message": "我住在成都，目标是减脂，膝盖偶尔不舒服。"},
        )

        assert response.status_code == 200
        memories = auth_client.get("/api/memory").json()["data"]
        assert {item["status"] for item in memories} == {"proposed"}
        assert {item["fact_key"] for item in memories} >= {"city", "training_goal", "injuries"}

        city_memory = next(item for item in memories if item["fact_key"] == "city")
        confirmed = auth_client.patch(
            f"/api/memory/{city_memory['id']}", json={"status": "confirmed"}
        )

        assert confirmed.status_code == 200
        assert confirmed.json()["data"]["status"] == "confirmed"

    def test_revoked_memory_is_hidden_by_default(self, auth_client):
        """验证撤销记忆默认隐藏，仅在显式参数下可查询。"""
        created = auth_client.post(
            "/api/memory",
            json={
                "fact_key": "custom_preference",
                "category": "custom",
                "value": {"value": "晚间训练"},
                "display_text": "偏好：晚间训练",
            },
        )
        memory_id = created.json()["data"]["id"]

        assert auth_client.delete(f"/api/memory/{memory_id}").status_code == 200
        assert auth_client.get("/api/memory").json()["data"] == []
        all_memories = auth_client.get("/api/memory?include_revoked=true").json()["data"]
        assert len(all_memories) == 1
        assert all_memories[0]["status"] == "revoked"

    def test_memory_api_cannot_read_or_update_another_users_memory(self, client):
        """即使知道记忆 ID，其他用户也不能通过管理接口访问。"""
        from app.core.auth import get_current_user
        from app.main import app
        from app.models import User

        created = client.post(
            "/api/memory",
            json={"fact_key": "custom", "display_text": "晚上训练", "value": {"value": "晚上"}},
        )
        assert created.status_code == 201
        memory_id = created.json()["data"]["id"]
        app.dependency_overrides[get_current_user] = lambda: User(id=2, username="other")
        assert client.get("/api/memory").json()["data"] == []
        assert (
            client.patch(f"/api/memory/{memory_id}", json={"status": "confirmed"}).status_code
            == 404
        )
        assert client.delete(f"/api/memory/{memory_id}").status_code == 404

    def test_provider_failure_returns_503_not_a_successful_empty_list(self, client, memory_backend):
        """故障必须可见，且不把外部异常正文返给用户。"""
        memory_backend.fail = True
        response = client.get("/api/memory")
        assert response.status_code == 503
        assert "private" not in response.text

    def test_revoke_failure_is_visible_and_keeps_current_state(self, client, memory_backend):
        """外部存储不可用时不能向用户声称撤销成功。"""
        created = client.post("/api/memory", json={"fact_key": "custom", "display_text": "散步"})
        memory_id = created.json()["data"]["id"]
        memory_backend.fail = True
        assert client.delete(f"/api/memory/{memory_id}").status_code == 503
        memory_backend.fail = False
        assert client.get("/api/memory").json()["data"][0]["status"] == "confirmed"

    def test_patch_preserves_omitted_expiration_and_clears_explicit_null(self, client):
        """管理接口区分未传到期时间和主动清除到期时间。"""
        created = client.post(
            "/api/memory",
            json={
                "fact_key": "custom",
                "display_text": "散步",
                "expires_at": "2030-01-01T12:00:00",
            },
        ).json()["data"]
        path = f"/api/memory/{created['id']}"
        unchanged = client.patch(path, json={"status": "confirmed"}).json()["data"]
        assert unchanged["expires_at"] == created["expires_at"]
        cleared = client.patch(path, json={"status": "confirmed", "expires_at": None}).json()[
            "data"
        ]
        assert cleared["expires_at"] is None

    def test_expiration_keeps_an_explicit_timezone_across_api_round_trip(self, client):
        """跨 UTC 午夜的到期时间应保留同一时刻，并返回可识别的时区。"""
        from datetime import datetime

        expiry = "2030-01-02T01:00:00+08:00"
        created = client.post(
            "/api/memory",
            json={"fact_key": "custom", "display_text": "散步", "expires_at": expiry},
        ).json()["data"]
        parsed = datetime.fromisoformat(created["expires_at"])
        assert parsed.tzinfo is not None
        assert parsed == datetime.fromisoformat(expiry)
        listed = client.get("/api/memory").json()["data"][0]
        assert listed["expires_at"] == created["expires_at"]
