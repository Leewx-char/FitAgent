"""
校验层 —— Pydantic 请求/响应模型。

职责：
  - 定义每个 API 端点的输入格式和输出格式
  - 通过 Field 做字段级校验（必填、长度限制）
  - model_config 允许从 ORM 对象直接转换，避免手动逐字段赋值

和 models.py 分离的原因：
  - 请求收明文密码，数据库存 bcrypt 哈希 —— 字段名不同
  - 响应不返回 password_hash —— 安全
  - 表结构变化不影响 API 返回格式 —— 解耦
"""
from datetime import datetime

from pydantic import BaseModel, Field
from pypika.clickhouse import dates_and_times


class UserRegister(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)

class UserLogin(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    city: str
    extra_info: str
    created_at: datetime

    model_config = {"from_attributes": True} # 允许从 ORM 对象直接转换


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

# ==================== 会话 ====================


class SessionCreate(BaseModel):
    title: str = "新对话"


class SessionResponse(BaseModel):
    id: str
    title: str
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

# ==================== 消息 ====================


class MessageCreate(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    created_at:datetime

    model_config = {"from_attributes": True}

# ==================== 对话 ====================


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None