"""COROS OAuth 授权、逐用户凭据和区域路由。"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session as DBSession

from app.core.settings import Settings, get_settings
from app.models import CorosAuthorizationRequest, CorosConnection


class CorosOAuthError(RuntimeError):
    """面向调用方的 COROS 授权失败，不包含上游敏感详情。"""


class CorosConfigurationError(CorosOAuthError):
    """COROS OAuth 运行配置不完整或不安全。"""


class CorosNotConnectedError(CorosOAuthError):
    """当前 FitAgent 用户尚未完成 COROS 授权。"""


class CorosReconnectionRequiredError(CorosOAuthError):
    """已保存的授权不可再刷新，用户必须重新连接。"""


class CorosUpstreamError(CorosOAuthError):
    """COROS OAuth 上游不可用或返回了不可用响应。"""


@dataclass(frozen=True)
class CorosAccessCredential:
    """仅在当前请求内持有的访问令牌。"""

    access_token: str
    issuer: str


@dataclass(frozen=True)
class CorosAuthorizationStart:
    """前端跳转到 COROS 授权页所需的最小数据。"""

    authorization_url: str
    expires_at: datetime


class CorosTokenCipher:
    """封装 Fernet，避免 token 加解密细节泄漏到业务调用方。"""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except (TypeError, ValueError) as error:
            raise CorosConfigurationError("COROS_TOKEN_ENCRYPTION_KEY 格式无效") from error

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise CorosReconnectionRequiredError("COROS 授权凭据无效，请重新连接") from error


class CorosOAuthService:
    """提供 OAuth 的小接口；路由只需启动、完成或读取凭据。"""

    _SCOPES = "openid offline_access mcp.tools"
    _STATE_TTL = timedelta(minutes=10)
    _REFRESH_SKEW = timedelta(seconds=60)

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = http_client or httpx.Client(
            timeout=self._settings.coros_oauth_timeout_seconds,
            follow_redirects=False,
        )
        self._now = now or (lambda: datetime.now(UTC).replace(tzinfo=None))

    def _cipher(self) -> CorosTokenCipher:
        key = self._settings.coros_token_encryption_key.strip()
        if not key:
            raise CorosConfigurationError("未配置 COROS_TOKEN_ENCRYPTION_KEY")
        return CorosTokenCipher(key)

    def _redirect_uri(self) -> str:
        value = self._settings.coros_oauth_redirect_uri.strip()
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise CorosConfigurationError("COROS_OAUTH_REDIRECT_URI 必须是 HTTPS 回调地址")
        return value

    def _gateway_issuer(self) -> str:
        parsed = urlsplit(self._settings.coros_mcp_gateway_url.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise CorosConfigurationError("COROS_MCP_GATEWAY_URL 必须是 HTTPS 地址")
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _normalize_issuer(value: object) -> str:
        issuer = str(value or "").rstrip("/")
        parsed = urlsplit(issuer)
        if parsed.scheme != "https" or not parsed.netloc:
            raise CorosUpstreamError("COROS 未返回有效区域授权地址")
        return issuer

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        reconnect_on_rejection: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """发送 OAuth 请求并把所有上游细节收敛为安全错误。"""

        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise CorosUpstreamError("COROS 授权服务暂不可用，请稍后重试") from error
        if response.status_code < 200 or response.status_code >= 300:
            if reconnect_on_rejection and response.status_code in {400, 401}:
                raise CorosReconnectionRequiredError("COROS 授权已失效，请重新连接")
            raise CorosUpstreamError("COROS 授权服务拒绝了请求，请稍后重试")
        try:
            payload = response.json()
        except ValueError as error:
            raise CorosUpstreamError("COROS 授权服务返回格式异常") from error
        if not isinstance(payload, dict):
            raise CorosUpstreamError("COROS 授权服务返回格式异常")
        return payload

    def _discover_issuer(self) -> str:
        payload = self._json_request(
            "GET", f"{self._gateway_issuer()}/.well-known/openid-configuration"
        )
        return self._normalize_issuer(payload.get("issuer"))

    def _register_client(self, issuer: str) -> str:
        payload = self._json_request(
            "POST",
            f"{issuer}/connect/register",
            json={
                "client_name": "FitAgent",
                "redirect_uris": [self._redirect_uri()],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": self._SCOPES,
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise CorosUpstreamError("COROS 授权服务未返回 client ID")
        return client_id

    @staticmethod
    def _pkce_verifier() -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    @staticmethod
    def _state_hash(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    @staticmethod
    def _mcp_url(issuer: str) -> str:
        return f"{issuer.rstrip('/')}/mcp"

    def _authorization_url(self, *, issuer: str, client_id: str, state: str, verifier: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": self._redirect_uri(),
                "scope": self._SCOPES,
                "code_challenge": self._pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "resource": self._mcp_url(issuer),
                "state": state,
            }
        )
        return f"{issuer}/oauth2/authorize?{query}"

    def start_authorization(self, db: DBSession, *, user_id: int) -> CorosAuthorizationStart:
        """为一名已登录用户创建一次可验证、短时有效的授权请求。"""

        cipher = self._cipher()
        issuer = self._discover_issuer()
        client_id = self._register_client(issuer)
        state = secrets.token_urlsafe(32)
        verifier = self._pkce_verifier()
        expires_at = self._now() + self._STATE_TTL
        db.query(CorosAuthorizationRequest).filter(
            CorosAuthorizationRequest.expires_at <= self._now()
        ).delete(synchronize_session=False)
        db.add(
            CorosAuthorizationRequest(
                state_hash=self._state_hash(state),
                user_id=user_id,
                issuer=issuer,
                client_id=client_id,
                verifier_ciphertext=cipher.encrypt(verifier),
                expires_at=expires_at,
            )
        )
        return CorosAuthorizationStart(
            authorization_url=self._authorization_url(
                issuer=issuer, client_id=client_id, state=state, verifier=verifier
            ),
            expires_at=expires_at,
        )

    def _exchange_code(
        self, *, issuer: str, client_id: str, code: str, verifier: str
    ) -> dict[str, Any]:
        return self._json_request(
            "POST",
            f"{issuer}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": self._redirect_uri(),
                "code_verifier": verifier,
            },
        )

    @staticmethod
    def _token_fields(payload: dict[str, Any]) -> tuple[str, str, int]:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise CorosUpstreamError("COROS 未返回访问令牌")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CorosUpstreamError("COROS 未返回刷新令牌")
        try:
            expires_in = max(1, int(payload.get("expires_in", 3600)))
        except (TypeError, ValueError) as error:
            raise CorosUpstreamError("COROS 返回的令牌有效期异常") from error
        return access_token, refresh_token, expires_in

    def complete_authorization(self, db: DBSession, *, state: str, code: str) -> int:
        """校验回调并将本次授权结果绑定到最初发起的 FitAgent 用户。"""

        if not state or not code:
            raise CorosOAuthError("COROS 授权回调缺少必要参数")
        request = (
            db.query(CorosAuthorizationRequest)
            .filter(CorosAuthorizationRequest.state_hash == self._state_hash(state))
            .one_or_none()
        )
        if request is None or request.expires_at <= self._now():
            if request is not None:
                db.delete(request)
                # 失败分支会抛异常，外层请求事务会回滚；这里必须先消费 state。
                db.commit()
            raise CorosOAuthError("COROS 授权已过期或无效，请重新连接")
        cipher = self._cipher()
        try:
            payload = self._exchange_code(
                issuer=request.issuer,
                client_id=request.client_id,
                code=code,
                verifier=cipher.decrypt(request.verifier_ciphertext),
            )
        except CorosOAuthError:
            # 回调已被消费，无论换码成败都不能留下可被重放的 state。
            db.delete(request)
            db.commit()
            raise
        access_token, refresh_token, expires_in = self._token_fields(payload)
        connection = (
            db.query(CorosConnection)
            .filter(CorosConnection.user_id == request.user_id)
            .one_or_none()
        )
        if connection is None:
            connection = CorosConnection(user_id=request.user_id)
            db.add(connection)
        connection.issuer = request.issuer
        connection.client_id = request.client_id
        connection.access_token_ciphertext = cipher.encrypt(access_token)
        connection.refresh_token_ciphertext = cipher.encrypt(refresh_token)
        connection.access_token_expires_at = self._now() + timedelta(seconds=expires_in)
        connection.status = "connected"
        user_id = request.user_id
        db.delete(request)
        return user_id

    def _connection(self, db: DBSession, user_id: int) -> CorosConnection:
        connection = (
            db.query(CorosConnection).filter(CorosConnection.user_id == user_id).one_or_none()
        )
        if connection is None:
            raise CorosNotConnectedError("尚未连接 COROS，请先在数据面板完成授权")
        if connection.status != "connected":
            raise CorosReconnectionRequiredError("COROS 授权已失效，请重新连接")
        return connection

    def _refresh_connection(self, connection: CorosConnection, cipher: CorosTokenCipher) -> str:
        refresh_token = cipher.decrypt(connection.refresh_token_ciphertext)
        try:
            payload = self._json_request(
                "POST",
                f"{connection.issuer}/oauth2/token",
                reconnect_on_rejection=True,
                data={
                    "grant_type": "refresh_token",
                    "client_id": connection.client_id,
                    "refresh_token": refresh_token,
                },
            )
            access_token = payload.get("access_token")
            next_refresh_token = payload.get("refresh_token", refresh_token)
            if not isinstance(access_token, str) or not access_token:
                raise CorosUpstreamError("COROS 未返回访问令牌")
            if not isinstance(next_refresh_token, str) or not next_refresh_token:
                raise CorosUpstreamError("COROS 未返回刷新令牌")
            expires_in = max(1, int(payload.get("expires_in", 3600)))
        except CorosUpstreamError:
            # 临时网络或服务端故障不能被误判为用户授权失效。
            raise
        except (CorosOAuthError, TypeError, ValueError) as error:
            raise CorosReconnectionRequiredError("COROS 授权已失效，请重新连接") from error
        connection.access_token_ciphertext = cipher.encrypt(access_token)
        connection.refresh_token_ciphertext = cipher.encrypt(next_refresh_token)
        connection.access_token_expires_at = self._now() + timedelta(seconds=expires_in)
        return access_token

    def get_access_credential(
        self, db: DBSession, *, user_id: int, force_refresh: bool = False
    ) -> CorosAccessCredential:
        """返回当前请求凭据；仅在临近过期或明确要求时刷新。"""

        connection = self._connection(db, user_id)
        cipher = self._cipher()
        try:
            if (
                not force_refresh
                and connection.access_token_expires_at > self._now() + self._REFRESH_SKEW
            ):
                token = cipher.decrypt(connection.access_token_ciphertext)
            else:
                token = self._refresh_connection(connection, cipher)
        except CorosReconnectionRequiredError:
            self.mark_reconnection_required(db, user_id=user_id)
            raise
        return CorosAccessCredential(access_token=token, issuer=connection.issuer)

    def mark_reconnection_required(self, db: DBSession, *, user_id: int) -> None:
        """在独立提交中持久化失效状态，避免外层错误事务回滚这一安全信号。"""

        connection = self.get_connection_status(db, user_id=user_id)
        if connection is None or connection.status == "reconnect_required":
            return
        connection.status = "reconnect_required"
        db.commit()

    def get_connection_status(self, db: DBSession, *, user_id: int) -> CorosConnection | None:
        """返回无敏感字段的连接状态所对应的 ORM 对象。"""

        return db.query(CorosConnection).filter(CorosConnection.user_id == user_id).one_or_none()

    def disconnect(self, db: DBSession, *, user_id: int) -> None:
        """清除本地凭据和待完成授权；官方没有公开远端 revoke 契约。"""

        db.query(CorosAuthorizationRequest).filter(
            CorosAuthorizationRequest.user_id == user_id
        ).delete(synchronize_session=False)
        connection = self.get_connection_status(db, user_id=user_id)
        if connection is not None:
            db.delete(connection)


@lru_cache(maxsize=1)
def get_coros_oauth_service() -> CorosOAuthService:
    """复用无用户状态的 OAuth 服务；令牌永不保存在服务实例中。"""

    return CorosOAuthService()
