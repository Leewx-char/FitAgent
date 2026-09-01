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
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

ResponseData = TypeVar("ResponseData")


class ApiResponse(BaseModel, Generic[ResponseData]):
    """普通 HTTP JSON 接口统一使用的响应信封。"""

    code: int
    messages: list[str] = Field(default_factory=list)
    data: ResponseData | None = None


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

    model_config = {"from_attributes": True}  # 允许从 ORM 对象直接转换


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
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== 对话 ====================


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None


class AgentToolCallResponse(BaseModel):
    sequence: int
    tool_name: str
    argument_shape: dict[str, str]
    status: str
    elapsed_ms: int
    detail: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("argument_shape", mode="before")
    @classmethod
    def parse_argument_shape(cls, value):
        """将持久化的工具参数形状 JSON 还原为字典。"""
        return json.loads(value) if isinstance(value, str) else value


class AgentRunResponse(BaseModel):
    id: str
    request_id: str
    mode: str
    status: str
    elapsed_ms: int
    tool_call_count: int
    created_at: datetime
    tool_calls: list[AgentToolCallResponse]

    model_config = {"from_attributes": True}


# ==================== 用户画像 ====================
class ProfileCreate(BaseModel):
    gender: str = Field(..., max_length=10)
    age: int = Field(..., ge=1, le=150)  # 必填，1-150
    height: int = Field(..., ge=50, le=300)  # 必填，50-300 cm
    weight: float = Field(..., gt=0, le=500)  # 必填，0-500 kg
    goal: str = Field("", max_length=20)  # 减脂/增肌/塑形/耐力提升/健康维护
    weekly_days: int = Field(3, ge=1, le=7)
    experience: str = Field("新手", max_length=20)  # 新手/有基础/资深
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
    health_data: "HealthDataSchema | None" = None


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

    # MySQL TEXT 列保存 JSON 字符串，返回 API 前需还原为 Python 原生类型。
    @field_validator("injuries", "diet_restrict", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        """将列表字段的 JSON 字符串还原为列表。"""
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("preferences", mode="before")
    @classmethod
    def parse_json_dict(cls, v):
        """将偏好字段的 JSON 字符串还原为字典。"""
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("health_data", mode="before")
    @classmethod
    def parse_json_health_data(cls, v):
        """将健康数据 JSON 字符串还原为空字典或字典。"""
        if v is None:
            return {}
        if isinstance(v, str):
            return json.loads(v)
        return v


# ==================== 健康文档上传 ====================
class HealthMetric(BaseModel):
    """从健康文档提取、可供用户核对的单项指标。"""

    value: float | int | str | None = None
    unit: str = ""

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> Any:
        """去除字符串指标值两端空白。"""
        if isinstance(value, str):
            return value.strip()
        return value


class HealthDataSchema(BaseModel):
    """上传确认和用户画像更新共用的健康数据持久化契约。"""

    height_cm: HealthMetric | None = None
    weight_kg: HealthMetric | None = None
    bmi: HealthMetric | None = None
    body_fat: HealthMetric | None = None
    heart_rate: HealthMetric | None = None
    blood_pressure: HealthMetric | None = None
    blood_sugar: HealthMetric | None = None
    cholesterol: HealthMetric | None = None
    alt: HealthMetric | None = None
    uric_acid: HealthMetric | None = None


class HealthMetricCandidate(BaseModel):
    page: int
    metric: HealthMetric


class HealthDocumentData(BaseModel):
    """健康文档上传成功时的业务数据。"""

    metrics: HealthDataSchema
    conflicts: dict[str, list[HealthMetricCandidate]] = Field(default_factory=dict)


ProfileUpdate.model_rebuild()


class FitnessSyncRequest(BaseModel):
    start_day: str = ""
    end_day: str = ""

    @field_validator("start_day", "end_day")
    @classmethod
    def validate_compact_date(cls, value: str) -> str:
        """校验非空同步日期为 YYYYMMDD 格式。"""
        if not value:
            return value
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("日期必须是 YYYYMMDD 格式") from exc
        return value

    @model_validator(mode="after")
    def validate_range(self):
        """确保同步起始日期不晚于结束日期。"""
        if self.start_day and self.end_day and self.start_day > self.end_day:
            raise ValueError("start_day 不能晚于 end_day")
        return self


class FitnessSyncResponse(BaseModel):
    """一次显式 Coros 到 MySQL 同步请求的结果。"""

    upserted: int = Field(ge=0)
    partial: bool = False
    unavailable_sources: list[str] = Field(default_factory=list)
    cached_source_counts: dict[str, int] = Field(default_factory=dict)


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
        """将日期对象或其他日期值格式化为字符串。"""
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)

    @field_validator("data", mode="before")
    @classmethod
    def parse_json(cls, v):
        """将持久化的数据 JSON 字符串还原为字典。"""
        if isinstance(v, str):
            return json.loads(v)
        return v


# ==================== 用户可控记忆 ====================


MemoryStatus = Literal["proposed", "confirmed", "revoked"]


class MemoryFactResponse(BaseModel):
    id: str
    source_message_id: int | None
    supersedes_id: str | None
    fact_key: str
    category: str
    value: dict[str, Any]
    display_text: str
    status: MemoryStatus
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("value", mode="before")
    @classmethod
    def parse_memory_value(cls, value):
        """将持久化的记忆值 JSON 字符串还原为字典。"""
        return json.loads(value) if isinstance(value, str) else value


class MemoryFactUpdate(BaseModel):
    status: Literal["confirmed", "revoked"]
    display_text: str | None = Field(default=None, max_length=300)
    expires_at: datetime | None = None


class MemoryFactCreate(BaseModel):
    """由用户在记忆页主动保存的条目；不接受模型直接写入。"""

    fact_key: str = Field(min_length=1, max_length=80)
    category: str = Field(default="custom", min_length=1, max_length=30)
    value: dict[str, Any] = Field(default_factory=dict)
    display_text: str = Field(min_length=1, max_length=300)
    expires_at: datetime | None = None


# ==================== 自适应训练计划 ====================


class PlanExercise(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sets: int = Field(ge=1, le=8)
    reps: str = Field(min_length=1, max_length=30)
    intensity: Literal["低", "中", "高"]
    notes: str = Field(default="", max_length=240)


class PlanDay(BaseModel):
    day_of_week: int = Field(ge=1, le=7)
    title: str = Field(min_length=1, max_length=80)
    focus: str = Field(min_length=1, max_length=120)
    kind: Literal["训练", "恢复", "休息"]
    exercises: list[PlanExercise] = Field(default_factory=list, max_length=8)
    notes: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_rest_day(self):
        """保证训练日有动作，恢复日和休息日不含动作。"""
        if self.kind != "训练" and self.exercises:
            raise ValueError("恢复日和休息日不能包含训练动作")
        if self.kind == "训练" and not self.exercises:
            raise ValueError("训练日必须包含至少一个动作")
        return self


class WeeklyTrainingPlan(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=80)
    days: list[PlanDay] = Field(min_length=1, max_length=7)
    rationale: list[str] = Field(default_factory=list, max_length=6)
    safety_notes: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_days(self):
        """保证周计划中每个星期仅出现一次。"""
        day_ids = [item.day_of_week for item in self.days]
        if len(day_ids) != len(set(day_ids)):
            raise ValueError("训练计划中不能出现重复星期")
        return self


class TrainingPlanGenerateRequest(BaseModel):
    week_start: date | None = None


class TrainingFeedbackCreate(BaseModel):
    day_of_week: int = Field(ge=1, le=7)
    completed: bool
    rpe: int | None = Field(default=None, ge=1, le=10)
    pain_score: int | None = Field(default=None, ge=0, le=10)
    notes: str = Field(default="", max_length=500)


class TrainingFeedbackResponse(TrainingFeedbackCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class TrainingPlanResponse(BaseModel):
    id: str
    week_start: date
    version: int
    status: Literal["draft", "active", "archived"]
    plan: WeeklyTrainingPlan
    safety: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    feedbacks: list[TrainingFeedbackResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_validator("plan", mode="before")
    @classmethod
    def parse_plan(cls, value):
        """将持久化的计划 JSON 字符串还原为计划模型输入。"""
        return json.loads(value) if isinstance(value, str) else value

    @field_validator("safety", mode="before")
    @classmethod
    def parse_safety(cls, value):
        """将持久化的安全评估 JSON 字符串还原为字典。"""
        return json.loads(value) if isinstance(value, str) else value
