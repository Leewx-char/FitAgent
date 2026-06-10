from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt # jose：JWT 的编解码库
from passlib.context import CryptContext # passlib：密码哈希库
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer # OAuth2PasswordBearer：FastAPI 提供的工具，自动从请求头的 Authorization: Bearer xxx 中提取 Token
from app.core.database import SessionLocal
from app.models import User
import os
from dotenv import load_dotenv


load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") # 密码哈希的上下文
def hash_password(plain: str) -> str: # 注册调用
    return pwd_context.hash(plain)
def verify_password(plain: str, hashed: str) -> bool: # 登录调用
    return pwd_context.verify(plain, hashed)

# 密钥就是"只有服务端知道的印章"——生成 Token 时盖章，验证 Token 时验章。没有密钥就无法伪造合法 Token。
def create_access_token(data: dict) -> str: # 生成令牌
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
def decode_access_token(token: str) -> dict: # 解析令牌
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
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

    """
        get_current_user
        本身就是一个
        Depends
        依赖函数，它的参数已经是
        Depends(oauth2_scheme)
        了。FastAPI
        的依赖注入可以嵌套，但
        get_db
        是通过
        yield 提供数据库会话的，在
        get_current_user
        里直接用更简单清晰——手动创建、手动关闭。
    """
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