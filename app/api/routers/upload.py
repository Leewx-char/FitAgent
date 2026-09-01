from fastapi import APIRouter, Depends, File, Request, UploadFile
from app.api.response import error_response, success_response
from app.core.auth import get_current_user
from app.models import User
from app.schemas import ApiResponse, HealthDocumentData
from app.services.doc_parser import HEALTH_CODE_INVALID_INPUT, HEALTH_CODE_OK, handle_upload
from app.utils.audit import audit_log
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/upload", tags=["upload"])


# 上传健康文档后提取结构化信息，用户确认后再通过个人资料接口保存。
@router.post("/health-doc", response_model=ApiResponse[HealthDocumentData])
@limiter.limit("5/minute")
async def upload_health_doc(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """解析当前用户上传的健康文档，并审计不含敏感内容的处理结果。"""
    if not file.filename:
        return error_response("文件名不能为空", status_code=400)

    file_bytes = await file.read()

    result = handle_upload(file_bytes, file.filename)

    result_code = result.get("code", HEALTH_CODE_INVALID_INPUT)
    # 上传健康文档涉及敏感数据，审计只保留操作状态，不记录文件名或体检内容。
    audit_log(
        "health_doc_upload",
        user_id=current_user.id,
        result="success" if result_code == HEALTH_CODE_OK else "fail",
        doc_code=result_code,
    )
    if result_code != HEALTH_CODE_OK:
        status_code = 400 if result_code == HEALTH_CODE_INVALID_INPUT else 422
        return error_response(result.get("messages", []), status_code=status_code)

    return success_response(HealthDocumentData.model_validate(result.get("data")))
