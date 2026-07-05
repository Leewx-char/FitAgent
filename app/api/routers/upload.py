from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from app.core.auth import get_current_user
from app.models import User
from app.services.doc_parser import handle_upload
from app.schemas import HealthDocUploadResponse
from app.schemas import HealthDataSchema
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

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
        raise HTTPException(status_code=400, detail="文件名不能为空")

    file_bytes = await file.read()

    result = handle_upload(file_bytes, file.filename)

    if result.get("status") == "ok":
        try:
            cleaned = HealthDataSchema(**result).model_dump(exclude_none=True)
            result = {
                "status": result.get("status"),
                "doc_type": result.get("doc_type", ""),
                **cleaned
            } # 保留 status/doc_type, 覆盖脏 data
        except Exception:
            result = {"status": "parse_failed", "message": "AI提取结果格式异常，请重试"}

    return HealthDocUploadResponse(
        status=result.get("status", "parse_failed"),
        doc_type=result.get("doc_type", ""),
        data=result if result.get("status") == "ok" else {},
        message=result.get("message", ""),
    )