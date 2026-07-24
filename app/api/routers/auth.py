from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_db
from app.models import User
from app.schemas import ApiResponse, TokenResponse, UserRegister, UserResponse
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.api.response import success_response
from app.utils.audit import audit_log
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=ApiResponse[UserResponse], status_code=201)
def register(body: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit_log("register", user_id=user.id, username=user.username)
    return success_response(UserResponse.model_validate(user), status_code=201)


@router.post("/login", response_model=ApiResponse[TokenResponse])
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        # 登录失败是关键安全事件（暴力破解线索），无 user_id 但记下尝试的用户名
        audit_log("login", result="fail", username=form_data.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    access_token = create_access_token(data={"user_id": user.id, "username": user.username})
    audit_log("login", user_id=user.id, username=user.username)
    return success_response(TokenResponse(access_token=access_token, token_type="bearer"))


@router.get("/me", response_model=ApiResponse[UserResponse])
def get_me(current_user: User = Depends(get_current_user)):  # FastAPI 自动执行 get_current_user
    return success_response(UserResponse.model_validate(current_user))
