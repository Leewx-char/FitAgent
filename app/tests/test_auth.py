import time


class TestAuth:
    def test_register_success(self, anon_client):
        username = f"newuser_{int(time.time() * 1000)}"
        resp = anon_client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "newpass123",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert set(data) == {"code", "messages", "data"}
        assert data["code"] == resp.status_code
        assert data["data"]["username"] == username

    def test_register_duplicate(self, anon_client):
        username = f"dupuser_{int(time.time() * 1000)}"
        anon_client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "pass123",
            },
        )
        # 同名再注册 → 应 400
        resp = anon_client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "pass123",
            },
        )
        assert resp.status_code == 400
        assert "已存在" in resp.json()["messages"][0]

    def test_login_success(self, anon_client):
        username = f"loginuser_{int(time.time() * 1000)}"
        anon_client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "pass123",
            },
        )
        resp = anon_client.post(
            "/api/auth/login",
            data={
                "username": username,
                "password": "pass123",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"code", "messages", "data"}
        assert data["code"] == resp.status_code
        assert "access_token" in data["data"]
        assert data["data"]["token_type"] == "bearer"

    def test_login_wrong_password(self, anon_client):
        username = f"wrongpw_{int(time.time() * 1000)}"
        anon_client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "pass123",
            },
        )
        resp = anon_client.post(
            "/api/auth/login",
            data={
                "username": username,
                "password": "wrongpass",
            },
        )
        assert resp.status_code == 401
        assert "错误" in resp.json()["messages"][0]

    def test_me_without_token(self, anon_client):
        resp = anon_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_token(self, auth_client):
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"].startswith("testuser_")
