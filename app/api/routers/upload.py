from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from app.core.auth import get_current_user
from app.models import User
from app.services.doc_parser import handle_upload
from app.schemas import HealthDocUploadResponse

router = APIRouter(prefix="/api/upload", tags=["upload"])

# 前端上传文件 → 后端AI提取 → 返回提取结果给前端 → 用户确认 → 调PUT /api/profile保存
# 上传接口
@router.post("/health-doc", response_model=HealthDocUploadResponse)
async def upload_health_doc(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    file_bytes = await file.read()

    result = handle_upload(file_bytes, file.filename)

    return HealthDocUploadResponse(
        status=result.get("status", "parse_failed"),
        doc_type=result.get("doc_type", ""),
        data=result if result.get("status") == "ok" else {},
        message=result.get("message", ""),
    )