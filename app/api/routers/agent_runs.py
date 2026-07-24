"""会话级 Agent 执行轨迹查询接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from app.api.response import success_response
from app.core.auth import get_current_user
from app.core.deps import get_db
from app.models import Session as SessionModel, User
from app.repositories.agent_trace_repository import AgentTraceRepository
from app.schemas import AgentRunResponse, ApiResponse

router = APIRouter(prefix="/api/sessions/{session_id}/agent-runs", tags=["agent-runs"])


@router.get("", response_model=ApiResponse[list[AgentRunResponse]])
def list_agent_runs(
    session_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: DBSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    runs = AgentTraceRepository.list_for_session(
        db, session_id=session_id, user_id=current_user.id, limit=limit
    )
    return success_response([AgentRunResponse.model_validate(run) for run in runs])
