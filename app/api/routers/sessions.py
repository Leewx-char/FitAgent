import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_db
from app.models import Session as SessionModel, User
from app.schemas import ApiResponse, SessionCreate, SessionResponse
from app.core.auth import get_current_user
from app.api.response import success_response
from app.utils.audit import audit_log

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=ApiResponse[list[SessionResponse]])
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """按最近更新时间倒序返回当前用户的全部会话。"""
    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.user_id == current_user.id)
        .order_by(SessionModel.updated_at.desc())
        .all()
    )
    return success_response([SessionResponse.model_validate(session) for session in sessions])


@router.post("", response_model=ApiResponse[SessionResponse], status_code=201)
def create_session(
    body: SessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为当前用户创建带随机标识的新会话。"""
    new_session = SessionModel(
        id=uuid.uuid4().hex[:32],
        title=body.title,
        user_id=current_user.id,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return success_response(SessionResponse.model_validate(new_session), status_code=201)


@router.delete("/{session_id}", response_model=ApiResponse[None])
def delete_session(
    session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """删除属于当前用户的指定会话并记录审计事件。"""
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(session)
    db.commit()
    audit_log("session_delete", user_id=current_user.id, session_id=session_id)
    return success_response(messages=["会话已删除"])


@router.patch("/{session_id}", response_model=ApiResponse[SessionResponse])
def update_session(
    session_id: str,
    body: SessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新属于当前用户的会话标题。"""
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.title = body.title
    db.commit()
    db.refresh(session)
    return success_response(SessionResponse.model_validate(session))
