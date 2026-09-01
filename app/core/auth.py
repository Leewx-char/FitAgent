from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt  # jose：JWT 的编解码库
from passlib.context import CryptContext  # passlib：密码哈希库
from fastapi import Depends, HTTPException, status
from fastapi.security import (
    OAuth2PasswordBearer,
)  # OAuth2PasswordBearer：FastAPI 提供的工具，自动从请求头的 Authorization: Bearer xxx 中提取 Token
from app.core.database import SessionLocal
from app.models import User
import os
from dotenv import load_dotenv


load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")  # 密码哈希的上下文


def hash_password(plain: str) -> str:  # 注册调用
    """对明文密码生成可存储的 bcrypt 哈希值。"""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:  # 登录调用
    """校验明文密码是否与已保存的哈希值匹配。"""
    return pwd_context.verify(plain, hashed)


# 密钥是服务端私有的签名材料，缺失它就无法伪造合法 Token。
def create_access_token(data: dict) -> str:  # 生成令牌
    """复制载荷并加入过期时间后签发 JWT 访问令牌。"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:  # 解析令牌
    """验证并解码 JWT；令牌无效时返回 ``None``。"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """从请求令牌解析用户身份，并返回仍存在的用户记录。"""
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id: int = payload.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
        )

    # 此依赖需在当前函数内独立创建并关闭会话，避免嵌套使用 yield 依赖。
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    return user
