import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_db
from app.models import User, UserProfile
from app.schemas import ApiResponse, ProfileCreate, ProfileResponse, ProfileUpdate
from app.core.auth import get_current_user
from app.api.response import success_response

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ApiResponse[ProfileResponse])
def get_profile(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """返回当前用户的个人画像，不存在时提示先创建。"""
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="用户画像不存在，请先创建")
    return success_response(ProfileResponse.model_validate(profile))


@router.post("", response_model=ApiResponse[ProfileResponse], status_code=201)
def create_profile(
    body: ProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为当前用户创建唯一的个人画像。"""
    existing = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户画像已存在，请用 PUT 更新")

    profile = UserProfile(
        user_id=current_user.id,
        gender=body.gender,
        age=body.age,
        height=body.height,
        weight=body.weight,
        goal=body.goal,
        weekly_days=body.weekly_days,
        experience=body.experience,
        injuries=json.dumps(body.injuries, ensure_ascii=False),  # list → 字符串
        diet_restrict=json.dumps(body.diet_restrict, ensure_ascii=False),
        preferences=json.dumps(body.preferences, ensure_ascii=False),
    )

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return success_response(ProfileResponse.model_validate(profile), status_code=201)


@router.put("", response_model=ApiResponse[ProfileResponse])
def update_profile(
    body: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """仅更新当前用户明确提交的个人画像字段。"""
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="用户画像不存在，请先创建")

    # 只取用户传入字段，避免 ProfileUpdate 的默认 None 覆盖已有值。
    update_data = body.model_dump(exclude_unset=True)

    # JSON 字段需序列化为字符串，并从普通字段循环中移除。
    if "injuries" in update_data:
        profile.injuries = json.dumps(update_data.pop("injuries"), ensure_ascii=False)
    if "diet_restrict" in update_data:
        profile.diet_restrict = json.dumps(update_data.pop("diet_restrict"), ensure_ascii=False)
    if "preferences" in update_data:
        profile.preferences = json.dumps(update_data.pop("preferences"), ensure_ascii=False)
    if "health_data" in update_data:
        profile.health_data = json.dumps(update_data.pop("health_data"), ensure_ascii=False)
    # 剩余的普通字段直接赋值
    for key, value in update_data.items():
        setattr(profile, key, value)

    db.commit()
    db.refresh(profile)
    return success_response(ProfileResponse.model_validate(profile))
