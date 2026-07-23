from fastapi import APIRouter, Depends, File, Request, UploadFile
from app.core.auth import get_current_user
from app.models import User
from app.schemas import HealthDocUploadResponse
from app.services.doc_parser import HEALTH_CODE_INVALID_INPUT, HEALTH_CODE_OK, handle_upload
from app.utils.audit import audit_log
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/upload", tags=["upload"])


# 前端上传文件 → 后端AI提取 → 返回提取结果给前端 → 用户确认 → 调PUT /api/profile保存
# 上传接口
@router.post("/health-doc", response_model=HealthDocUploadResponse)
@limiter.limit("5/minute")
async def upload_health_doc(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    if not file.filename:
        return HealthDocUploadResponse(
            code=HEALTH_CODE_INVALID_INPUT,
            messages=["文件名不能为空"],
        )

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
    return HealthDocUploadResponse.model_validate(result)
