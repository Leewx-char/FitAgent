import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from server.deps import get_db
from server.models import Session as SessionModel, User
from server.schemas import SessionCreate, SessionResponse
from server.auth import get_current_user

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

@router.get("", response_model=list[SessionResponse])
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.user_id == current_user.id)
        .order_by(SessionModel.updated_at.desc())
        .all()
    )
    return sessions

@router.post("", response_model=SessionResponse)
def create_session(body: SessionCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    new_session = SessionModel(
        id=uuid.uuid4().hex[:32],
        title=body.title,
        user_id=current_user.id,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return new_session

@router.delete("/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(session)
    db.commit()
    return {"message": "会话已删除"}

@router.patch("/{session_id}", response_model=SessionResponse)
def update_session(session_id: str, body: SessionCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
    return session