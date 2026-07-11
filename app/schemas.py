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
import json
from datetime import datetime
from pydantic import field_validator
from pydantic import BaseModel, Field


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

# ==================== 用户画像 ====================
class ProfileCreate(BaseModel):
    gender: str = Field(..., max_length=10)
    age: int = Field(..., ge=1, le=150)              # 必填，1-150
    height: int = Field(..., ge=50, le=300)          # 必填，50-300 cm
    weight: float = Field(..., gt=0, le=500)        # 必填，0-500 kg
    goal: str = Field("", max_length=20)  # 减脂/增肌/塑形/耐力提升/健康维护
    weekly_days: int = Field(3, ge=1, le=7)
    experience: str = Field("新手", max_length=20)   # 新手/有基础/资深
    injuries: list[str] = Field(default_factory=list)
    diet_restrict: list[str] = Field(default_factory=list)
    preferences: dict = Field(default_factory=dict)

class ProfileUpdate(BaseModel):
    gender: str | None = None
    age: int | None = None
    height: int | None = None
    weight: float | None = None
    goal: str | None = None
    weekly_days: int | None = None
    experience: str | None = None
    injuries: list[str] | None = None
    diet_restrict: list[str] | None = None
    preferences: dict | None = None
    health_data: dict | None = None

class ProfileResponse(BaseModel):
    id: int
    user_id: int
    gender: str
    age: int | None
    height: int | None
    weight: float | None
    goal: str
    weekly_days: int
    experience: str
    injuries: list[str]
    diet_restrict: list[str]
    preferences: dict
    health_data: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    # ：MySQL 的 TEXT 列存 JSON 字符串，但 API 返回给前端时要转成 Python 原生类型
    @field_validator("injuries", "diet_restrict", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("preferences", mode="before")
    @classmethod
    def parse_json_dict(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("health_data", mode="before")
    @classmethod
    def parse_json_health_data(cls, v):
        if v is None:
            return {}
        if isinstance(v, str):
            return json.loads(v)
        return v

# ==================== 健康文档上传 ====================
class HealthDocUploadResponse(BaseModel):
    status: str # ok / unrelated / parse_failed / encrypted / error
    doc_type: str = ""
    data: dict = {} # 提取的健康数据 JSON
    message: str = ""

# 校验体检单数据，防止传入脏数据
class HealthDataSchema(BaseModel):
    height_cm: float | None = None
    weight_kg: float | None = None
    bmi: float | None = None
    body_fat: float | None = None
    heart_rate: int | None = None
    blood_pressure: str | None = None
    blood_sugar: float | None = None
    cholesterol: float | None = None
    alt: float | None = None
    uric_acid: float | None = None
    other_findings: list | None = None

    @field_validator("heart_rate", mode="before")
    @classmethod
    def parse_int(cls, v):
        if isinstance(v, str) and v.strip():
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return v
        return v

    @field_validator("height_cm", "weight_kg", "bmi", "body_fat",
                    "blood_sugar", "cholesterol", "alt", "uric_acid", mode="before")
    @classmethod
    def parse_float(cls, v):
        if isinstance(v, str) and v.strip():
            try:
                return float(v)
            except (ValueError, TypeError):
                return v
        return v

class FitnessSyncRequest(BaseModel):
    start_day: str = ""
    end_day: str = ""

class FitnessDataResponse(BaseModel):
    id: int
    user_id: int
    date: str
    data_type: str
    data: dict
    created_at: datetime
    # 把 SQLAlchemy ORM 对象转成 JSON 返回
    model_config = {"from_attributes": True}

    @field_validator("date", mode="before")
    @classmethod
    def format_date(cls, v):
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)

    @field_validator("data", mode="before")
    @classmethod
    def parse_json(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v