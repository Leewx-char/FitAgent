"""用户可控长期记忆生命周期的集成测试。"""


class TestMemory:
    def test_chat_creates_proposals_but_not_confirmed_memories(self, auth_client, agent_mock):
        """验证聊天只产生待确认记忆，确认后才变为 confirmed 状态。"""
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
