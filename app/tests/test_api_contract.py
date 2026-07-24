"""普通 HTTP JSON 接口统一响应契约测试。"""


def assert_envelope(payload: dict, *, status_code: int) -> None:
    """确认所有普通 JSON 响应都包含相同的顶层字段。"""

    assert set(payload) == {"code", "messages", "data"}
    assert payload["code"] == status_code
    assert isinstance(payload["messages"], list)


def test_request_validation_uses_unified_envelope(anon_client):
    response = anon_client.post("/api/auth/register", json={"username": "a", "password": "short"})

    assert response.status_code == 422
    payload = response.json()
    assert_envelope(payload, status_code=response.status_code)
    assert payload["data"] is None
    assert payload["messages"]


def test_health_check_uses_unified_envelope(anon_client):
    response = anon_client.get("/api/health")

    assert response.status_code == 200
    assert_envelope(response.json(), status_code=response.status_code)
    assert response.json()["data"] == {"status": "ok"}


def test_resource_endpoints_use_unified_envelope(auth_client):
    profile_response = auth_client.post(
        "/api/profile",
        json={
            "gender": "男",
            "age": 25,
            "height": 175,
            "weight": 70,
        },
    )
    assert profile_response.status_code == 201
    assert_envelope(profile_response.json(), status_code=profile_response.status_code)
    assert profile_response.json()["data"]["height"] == 175

    sessions_response = auth_client.get("/api/sessions")
    assert sessions_response.status_code == 200
    assert_envelope(sessions_response.json(), status_code=sessions_response.status_code)
    assert isinstance(sessions_response.json()["data"], list)

    fitness_response = auth_client.get("/api/fitness/activities")
    assert fitness_response.status_code == 200
    assert_envelope(fitness_response.json(), status_code=fitness_response.status_code)
    assert fitness_response.json()["data"] == []
