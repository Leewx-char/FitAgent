from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_db
from app.models import Session as SessionModel, Message, User
from app.schemas import ApiResponse, MessageResponse
from app.core.auth import get_current_user
from app.api.response import success_response

router = APIRouter(prefix="/api/sessions/{session_id}/messages", tags=["messages"])


@router.get("", response_model=ApiResponse[list[MessageResponse]])
def list_messages(
    session_id: str,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """验证会话归属后，分页返回会话消息。"""
    # 先验会话归属
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 再取信息
    messages = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return success_response([MessageResponse.model_validate(message) for message in messages])
