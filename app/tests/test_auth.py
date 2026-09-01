import time


class TestAuth:
    def test_register_success(self, anon_client):
        """验证新用户名可注册，并在响应中返回该用户名。"""
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
        """验证重复注册同一用户名会返回已存在错误。"""
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
        """验证已注册用户可登录并获得 Bearer 访问令牌。"""
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
        """验证错误密码登录被拒绝并返回错误提示。"""
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
        """验证未携带令牌访问当前用户接口会被拒绝。"""
        resp = anon_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_token(self, auth_client):
        """验证有效令牌可读取当前登录测试用户。"""
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"].startswith("testuser_")
