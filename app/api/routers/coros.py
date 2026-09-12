"""COROS 浏览器 OAuth 连接管理接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.response import success_response
from app.core.auth import get_current_user
from app.core.deps import get_db
from app.core.settings import get_settings
from app.models import User
from app.schemas import ApiResponse, CorosAuthorizationStartResponse, CorosConnectionResponse
from app.services.coros_oauth import CorosOAuthError, get_coros_oauth_service

router = APIRouter(prefix="/api/coros", tags=["coros"])


@router.post("/connect", response_model=ApiResponse[CorosAuthorizationStartResponse])
def connect_coros(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """创建当前用户专属的 OAuth state、PKCE verifier 和浏览器授权地址。"""

    try:
        authorization = get_coros_oauth_service().start_authorization(db, user_id=current_user.id)
    except CorosOAuthError as error:
        raise HTTPException(status_code=503, detail="暂时无法发起 COROS 授权") from error
    return success_response(
        CorosAuthorizationStartResponse(
            authorization_url=authorization.authorization_url,
            expires_at=authorization.expires_at,
        )
    )


@router.get("/callback", include_in_schema=False)
def coros_callback(
    state: str = Query(default=""),
    code: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """完成一次短期 OAuth 请求后回跳固定 Dashboard 地址。"""

    if error:
        raise HTTPException(status_code=400, detail="COROS 授权未完成")
    try:
        get_coros_oauth_service().complete_authorization(db, state=state, code=code)
    except CorosOAuthError as exc:
        raise HTTPException(status_code=400, detail="COROS 授权回调无效或已过期") from exc
    return RedirectResponse(
        url=get_settings().coros_oauth_post_connect_redirect_uri,
        status_code=303,
    )


@router.get("/connection", response_model=ApiResponse[CorosConnectionResponse])
def get_coros_connection(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """读取当前用户连接状态，不返回 issuer、client 或 token。"""

    connection = get_coros_oauth_service().get_connection_status(db, user_id=current_user.id)
    status = connection.status if connection is not None else "not_connected"
    return success_response(CorosConnectionResponse(connected=status == "connected", status=status))


@router.delete("/connection", response_model=ApiResponse[dict[str, bool]])
def disconnect_coros(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """仅清除本地加密凭据；官方未公开可依赖的远端撤销契约。"""

    get_coros_oauth_service().disconnect(db, user_id=current_user.id)
    return success_response({"disconnected": True})
