"""用户可控长期记忆 API。

模型只能产生 ``proposed`` 候选；只有此路由代表的用户操作才能使记忆进入
``confirmed`` 状态并在后续 Agent 对话中被读取。
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.response import success_response
from app.core.auth import get_current_user
from app.core.deps import get_db
from app.models import MemoryFact, User
from app.schemas import (
    ApiResponse,
    MemoryFactCreate,
    MemoryFactResponse,
    MemoryFactUpdate,
)
from app.services.memory_service import MemoryService


router = APIRouter(prefix="/api/memory", tags=["memory"])
memory_service = MemoryService()


def _serialize(memory: MemoryFact) -> MemoryFactResponse:
    """将记忆 ORM 对象转换为公开响应模型。"""
    return MemoryFactResponse.model_validate(memory)


@router.get("", response_model=ApiResponse[list[MemoryFactResponse]])
def list_memories(
    include_revoked: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户的记忆，可选择包含已撤销项。"""
    memories = memory_service.list_for_user(
        db, user_id=current_user.id, include_revoked=include_revoked
    )
    return success_response([_serialize(memory) for memory in memories])


@router.post("", response_model=ApiResponse[MemoryFactResponse], status_code=201)
def create_memory(
    body: MemoryFactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新当前用户记忆的显示文本、到期时间或确认状态。"""
    """保存用户主动输入的已确认记忆。"""

    memory = MemoryFact(
        id=uuid.uuid4().hex,
        user_id=current_user.id,
        fact_key=body.fact_key,
        category=body.category,
        value=json.dumps(body.value, ensure_ascii=False, sort_keys=True),
        display_text=body.display_text,
        status="confirmed",
        expires_at=body.expires_at,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return success_response(_serialize(memory), status_code=201)


@router.patch("/{memory_id}", response_model=ApiResponse[MemoryFactResponse])
def update_memory(
    memory_id: str,
    body: MemoryFactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销当前用户指定的记忆。"""
    memory = (
        db.query(MemoryFact)
        .filter(MemoryFact.id == memory_id, MemoryFact.user_id == current_user.id)
        .one_or_none()
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="记忆不存在")

    if body.display_text is not None:
        memory.display_text = body.display_text
    if "expires_at" in body.model_fields_set:
        memory.expires_at = body.expires_at
    try:
        if body.status == "confirmed":
            memory_service.confirm(db, memory)
        else:
            memory_service.revoke(memory)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(memory)
    return success_response(_serialize(memory))


@router.delete("/{memory_id}", response_model=ApiResponse[MemoryFactResponse])
def revoke_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销当前用户指定的记忆。"""
    memory = (
        db.query(MemoryFact)
        .filter(MemoryFact.id == memory_id, MemoryFact.user_id == current_user.id)
        .one_or_none()
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    memory_service.revoke(memory)
    db.commit()
    db.refresh(memory)
    return success_response(_serialize(memory))
