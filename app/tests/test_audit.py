import json
import logging
import time

import pytest


@pytest.fixture
def audit_records():
    """捕获 audit logger 写出的每条 JSON，供断言。
    审计 logger propagate=False，但挂在它身上的 handler 仍会触发。"""
    records = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(json.loads(record.getMessage()))

    audit_logger = logging.getLogger("audit")
    handler = _CaptureHandler()
    audit_logger.addHandler(handler)
    yield records
    audit_logger.removeHandler(handler)


class TestAudit:
    def test_login_fail_audited(self, anon_client, audit_records):
        """登录失败 → 审计 result=fail，记下尝试的用户名，user_id 为空"""
        anon_client.post(
            "/api/auth/login",
            data={
                "username": "ghost_user_xyz",
                "password": "wrongpass",
            },
        )
        fails = [r for r in audit_records if r["action"] == "login" and r["result"] == "fail"]
        assert fails
        assert fails[-1]["username"] == "ghost_user_xyz"
        assert fails[-1]["user_id"] is None

    def test_register_and_login_success_audited(self, anon_client, audit_records):
        """注册 + 登录成功 → 各产生一条审计（result=success，含 user_id）"""
        username = f"audituser_{int(time.time() * 1000)}"
        anon_client.post("/api/auth/register", json={"username": username, "password": "pass123"})
        anon_client.post("/api/auth/login", data={"username": username, "password": "pass123"})

        regs = [r for r in audit_records if r["action"] == "register"]
        assert regs and regs[-1]["username"] == username and regs[-1]["user_id"] is not None

        logins = [r for r in audit_records if r["action"] == "login" and r["result"] == "success"]
        assert logins and logins[-1]["username"] == username

    def test_session_delete_audited(self, auth_client, audit_records):
        """删除会话 → 审计 action=session_delete，记下 session_id"""
        sid = auth_client.post("/api/sessions", json={"title": "待删除"}).json()["data"]["id"]
        auth_client.delete(f"/api/sessions/{sid}")
        dels = [r for r in audit_records if r["action"] == "session_delete"]
        assert dels and dels[-1]["session_id"] == sid
