"""用户可控长期记忆 API；mem0 是唯一长期记忆存储。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.response import success_response
from app.core.auth import get_current_user
from app.models import User
from app.schemas import ApiResponse, MemoryFactCreate, MemoryFactResponse, MemoryFactUpdate
from app.services.memory_service import MemoryService, MemoryUnavailableError, memory_payload

router = APIRouter(prefix="/api/memory", tags=["memory"])
memory_service = MemoryService()


def _invoke(operation, **arguments):
    """把业务错误映射为稳定的 HTTP 错误，不暴露平台异常正文。"""
    try:
        return operation(**arguments)
    except MemoryUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _serialize(record):
    """保留原管理页面的响应字段，隔离内部元数据结构。"""
    return MemoryFactResponse.model_validate(memory_payload(record))


@router.get("", response_model=ApiResponse[list[MemoryFactResponse]])
def list_memories(
    include_revoked: bool = Query(default=False), current_user: User = Depends(get_current_user)
):
    """查询当前用户的记忆，包括候选和已确认项。"""
    rows = _invoke(
        memory_service.list_for_user, user_id=current_user.id, include_revoked=include_revoked
    )
    return success_response([_serialize(row) for row in rows])


@router.post("", response_model=ApiResponse[MemoryFactResponse], status_code=201)
def create_memory(body: MemoryFactCreate, current_user: User = Depends(get_current_user)):
    """用户主动添加的内容直接确认，不通过 LLM 改写。"""
    row = _invoke(
        memory_service.create_memory,
        user_id=current_user.id,
        text=body.display_text,
        fact_key=body.fact_key,
        category=body.category,
        value=body.value,
        expires_at=body.expires_at,
    )
    return success_response(_serialize(row), status_code=201)


@router.patch("/{memory_id}", response_model=ApiResponse[MemoryFactResponse])
def update_memory(
    memory_id: str, body: MemoryFactUpdate, current_user: User = Depends(get_current_user)
):
    """编辑或确认当前用户的记忆；省略到期字段时保留原值。"""
    row = _invoke(
        memory_service.update_memory,
        user_id=current_user.id,
        memory_id=memory_id,
        **body.model_dump(exclude_unset=True),
    )
    return success_response(_serialize(row))


@router.delete("/{memory_id}", response_model=ApiResponse[MemoryFactResponse])
def revoke_memory(memory_id: str, current_user: User = Depends(get_current_user)):
    """把记忆标记为撤销，使后续查询排除该条记录。"""
    row = _invoke(
        memory_service.update_memory, user_id=current_user.id, memory_id=memory_id, status="revoked"
    )
    return success_response(_serialize(row))
