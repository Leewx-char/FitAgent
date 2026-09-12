"""COROS OAuth 的 PKCE、加密与逐用户绑定测试。"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from app.core.settings import Settings
from app.models import CorosAuthorizationRequest, CorosConnection
from app.services.coros_oauth import (
    CorosOAuthError,
    CorosOAuthService,
    CorosReconnectionRequiredError,
    CorosTokenCipher,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeHttpClient:
    """根据 OAuth URL 返回公开 discovery、DCR 和 token 响应。"""

    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/.well-known/openid-configuration"):
            return FakeResponse({"issuer": "https://mcpcn.coros.com"})
        if url.endswith("/connect/register"):
            return FakeResponse({"client_id": "client-for-test"})
        if url.endswith("/oauth2/token"):
            return FakeResponse(
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                }
            )
        raise AssertionError(f"unexpected URL: {url}")


class FakeQuery:
    def __init__(self, value=None):
        self.value = value

    def filter(self, *_args):
        return self

    def one_or_none(self):
        return self.value

    def delete(self, **_kwargs):
        return 0


class FakeDatabase:
    def __init__(self, authorization_request=None, connection=None):
        self.authorization_request = authorization_request
        self.connection = connection
        self.added = []
        self.deleted = []
        self.commit_count = 0

    def query(self, model):
        if model is CorosAuthorizationRequest:
            return FakeQuery(self.authorization_request)
        if model is CorosConnection:
            return FakeQuery(self.connection)
        raise AssertionError(f"unexpected model: {model}")

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.commit_count += 1


def _settings(key):
    return Settings(
        coros_mcp_gateway_url="https://mcp.coros.com/mcp",
        coros_oauth_redirect_uri="https://api.example.com/api/coros/callback",
        coros_token_encryption_key=key,
    )


def test_start_authorization_stores_hash_and_encrypted_pkce_only():
    """state、verifier 明文只出现于当次跳转 URL，数据库不保存其明文。"""

    key = Fernet.generate_key().decode()
    database = FakeDatabase()
    service = CorosOAuthService(settings=_settings(key), http_client=FakeHttpClient())

    start = service.start_authorization(database, user_id=42)

    request = database.added[0]
    query = parse_qs(urlsplit(start.authorization_url).query)
    assert request.user_id == 42
    assert len(request.state_hash) == 64
    assert request.state_hash != query["state"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://api.example.com/api/coros/callback"]
    assert query["state"][0] not in request.verifier_ciphertext
    assert CorosTokenCipher(key).decrypt(request.verifier_ciphertext)


def test_callback_binds_token_to_request_user_and_deletes_one_time_state():
    """回调只依据 state 查到的用户保存加密 token，不能接受浏览器传入 user_id。"""

    key = Fernet.generate_key().decode()
    service = CorosOAuthService(settings=_settings(key), http_client=FakeHttpClient())
    state = "test-state"
    request = CorosAuthorizationRequest(
        state_hash=service._state_hash(state),
        user_id=99,
        issuer="https://mcpcn.coros.com",
        client_id="client-for-test",
        verifier_ciphertext=CorosTokenCipher(key).encrypt("verifier"),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
    )
    database = FakeDatabase(authorization_request=request)

    user_id = service.complete_authorization(database, state=state, code="authorization-code")

    connection = database.added[0]
    assert user_id == 99
    assert connection.user_id == 99
    assert connection.access_token_ciphertext != "access-token"
    assert CorosTokenCipher(key).decrypt(connection.refresh_token_ciphertext) == "refresh-token"
    assert database.deleted == [request]


def test_expired_state_is_rejected_and_deleted():
    """过期或重放 state 不能换取 token。"""

    key = Fernet.generate_key().decode()
    service = CorosOAuthService(settings=_settings(key), http_client=FakeHttpClient())
    request = CorosAuthorizationRequest(
        state_hash=service._state_hash("expired"),
        user_id=1,
        issuer="https://mcpcn.coros.com",
        client_id="client-for-test",
        verifier_ciphertext=CorosTokenCipher(key).encrypt("verifier"),
        expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
    )
    database = FakeDatabase(authorization_request=request)

    with pytest.raises(CorosOAuthError, match="过期"):
        service.complete_authorization(database, state="expired", code="code")

    assert database.deleted == [request]
    assert database.commit_count == 1


def test_failed_token_exchange_consumes_state_before_request_rollback():
    """错误授权码也只能使用一次，防止同一 state 被回放。"""

    class RejectedCodeHttp(FakeHttpClient):
        def request(self, method, url, **kwargs):
            if url.endswith("/oauth2/token"):
                return FakeResponse({}, status_code=400)
            return super().request(method, url, **kwargs)

    key = Fernet.generate_key().decode()
    service = CorosOAuthService(settings=_settings(key), http_client=RejectedCodeHttp())
    state = "bad-code-state"
    request = CorosAuthorizationRequest(
        state_hash=service._state_hash(state),
        user_id=3,
        issuer="https://mcpcn.coros.com",
        client_id="client-for-test",
        verifier_ciphertext=CorosTokenCipher(key).encrypt("verifier"),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
    )
    database = FakeDatabase(authorization_request=request)

    with pytest.raises(CorosOAuthError):
        service.complete_authorization(database, state=state, code="bad-code")

    assert database.deleted == [request]
    assert database.commit_count == 1


def test_refresh_rejection_marks_only_that_users_connection_for_reconnect():
    """刷新令牌被拒绝时，连接状态变更且不会暴露 token。"""

    class RejectedRefreshHttp(FakeHttpClient):
        def request(self, method, url, **kwargs):
            if url.endswith("/oauth2/token"):
                return FakeResponse({}, status_code=401)
            return super().request(method, url, **kwargs)

    key = Fernet.generate_key().decode()
    connection = CorosConnection(
        user_id=9,
        issuer="https://mcpcn.coros.com",
        client_id="client-for-test",
        access_token_ciphertext=CorosTokenCipher(key).encrypt("old-access"),
        refresh_token_ciphertext=CorosTokenCipher(key).encrypt("old-refresh"),
        access_token_expires_at=datetime.now(UTC).replace(tzinfo=None),
        status="connected",
    )
    service = CorosOAuthService(settings=_settings(key), http_client=RejectedRefreshHttp())

    with pytest.raises(CorosReconnectionRequiredError):
        service.get_access_credential(FakeDatabase(connection=connection), user_id=9)

    assert connection.status == "reconnect_required"
