"""Structured, evidence-aware and safety-bounded weekly plan generation."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session as DBSession

from app.models import TrainingFeedback, TrainingPlan, UserProfile
from app.schemas import TrainingFeedbackCreate, WeeklyTrainingPlan
from app.services.factory import get_chat_model
from app.services.fitness_insights import FitnessSnapshot, load_fitness_snapshot
from app.services.rag_service import RagSummarizeService
from app.utils.prompt_loader import load_training_plan_prompt


class PlanGenerationError(RuntimeError):
    """An expected, user-facing generation failure that never persists a partial plan."""


def _parse_json_field(value: str | dict | list | None, default):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _extract_json(content: object) -> dict[str, Any]:
    """Accept plain JSON or a fenced model response, then reject any non-object response."""

    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PlanGenerationError("模型未返回有效的结构化训练计划，请重试") from error
    if not isinstance(result, dict):
        raise PlanGenerationError("模型返回的训练计划格式无效，请重试")
    return result


@dataclass(frozen=True)
class SafetyAssessment:
    maximum_intensity: str
    constraints: list[str]
    signals: list[str]
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_intensity": self.maximum_intensity,
            "constraints": self.constraints,
            "signals": self.signals,
            "disclaimer": self.disclaimer,
        }


class TrainingSafetyPolicy:
    """Deterministic policy gate. The model may plan only within this envelope."""

    @staticmethod
    def assess(
        profile: UserProfile,
        snapshot: FitnessSnapshot,
        recent_feedback: list[TrainingFeedback],
    ) -> SafetyAssessment:
        injuries = _parse_json_field(profile.injuries, [])
        constraints: list[str] = []
        signals: list[str] = []
        maximum_intensity = "高" if profile.experience == "advanced" else "中"

        if isinstance(injuries, list) and injuries:
            maximum_intensity = "低"
            constraints.append("存在用户记录的伤病/不适，本周仅安排低强度且无痛范围内训练")
        if snapshot.max_training_load_ratio is not None and snapshot.max_training_load_ratio > 1.3:
            maximum_intensity = "低"
            signals.append("近4周训练负荷比偏高")
        if snapshot.avg_sleep_hours is not None and snapshot.avg_sleep_hours < 6:
            maximum_intensity = "低"
            signals.append("近4周平均睡眠不足6小时")
        if snapshot.avg_tired_rate is not None and snapshot.avg_tired_rate >= 7:
            maximum_intensity = "低"
            signals.append("近4周平均疲劳度较高")

        pain_scores = [item.pain_score for item in recent_feedback if item.pain_score is not None]
        rpes = [item.rpe for item in recent_feedback if item.rpe is not None]
        if any(score >= 4 for score in pain_scores):
            maximum_intensity = "低"
            signals.append("最近执行反馈中存在中度以上疼痛")
        elif any(score >= 9 for score in rpes) and maximum_intensity == "高":
            maximum_intensity = "中"
            signals.append("最近执行反馈中存在极高主观用力程度")

        if not signals and not constraints:
            signals.append("未发现需要自动降级的恢复信号")
        return SafetyAssessment(
            maximum_intensity=maximum_intensity,
            constraints=constraints,
            signals=signals,
            disclaimer="训练计划不是医疗建议；疼痛加重、胸痛、头晕或异常不适时应停止训练并咨询专业人士。",
        )


class TrainingPlanService:
    """Orchestrate retrieval, constrained LLM generation and persistent feedback state."""

    def __init__(self, *, model=None, rag_service: RagSummarizeService | None = None) -> None:
        self._model = model
        self._rag_service = rag_service

    @property
    def model(self):
        return self._model or get_chat_model()

    @property
    def rag_service(self) -> RagSummarizeService:
        return self._rag_service or RagSummarizeService()

    @staticmethod
    def _load_profile(db: DBSession, user_id: int) -> UserProfile:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
        if profile is None:
            raise PlanGenerationError("请先完善健身画像，再生成训练计划")
        return profile

    @staticmethod
    def _recent_feedback(db: DBSession, user_id: int) -> list[TrainingFeedback]:
        return (
            db.query(TrainingFeedback)
            .join(TrainingPlan, TrainingFeedback.plan_id == TrainingPlan.id)
            .filter(TrainingPlan.user_id == user_id)
            .order_by(TrainingFeedback.created_at.desc())
            .limit(14)
            .all()
        )

    @staticmethod
    def _profile_summary(profile: UserProfile) -> dict[str, Any]:
        return {
            "age": profile.age,
            "weight_kg": profile.weight,
            "goal": profile.goal or "健康管理",
            "weekly_days": profile.weekly_days,
            "experience": profile.experience,
            "injuries": _parse_json_field(profile.injuries, []),
            "preferences": _parse_json_field(profile.preferences, {}),
        }

    def _retrieve_evidence(
        self, profile: UserProfile, safety: SafetyAssessment
    ) -> tuple[str, list[str]]:
        query = f"{profile.goal or '健康管理'} 每周训练计划 恢复 伤病预防"
        try:
            context = self.rag_service.build_context(query)
        except Exception as error:
            raise PlanGenerationError(
                "训练知识库暂不可用，无法生成带证据的计划，请稍后重试"
            ) from error
        evidence_ids = (
            [hit.evidence_id for hit in context.result.hits[:6]] if context.result else []
        )
        safety_text = "；".join(safety.constraints + safety.signals)
        return context.content + f"\n\n安全信号：{safety_text}", evidence_ids

    @staticmethod
    def _validate_plan(
        plan: WeeklyTrainingPlan,
        *,
        weekly_days: int,
        safety: SafetyAssessment,
        available_evidence_ids: list[str],
    ) -> None:
        expected_days = set(range(1, 8))
        if {item.day_of_week for item in plan.days} != expected_days:
            raise PlanGenerationError("模型未覆盖完整的一周计划，请重试")
        training_days = [item for item in plan.days if item.kind == "训练"]
        if len(training_days) > weekly_days:
            raise PlanGenerationError("模型生成的训练天数超出用户设定，请重试")
        intensity_rank = {"低": 1, "中": 2, "高": 3}
        max_rank = intensity_rank[safety.maximum_intensity]
        if any(
            intensity_rank[exercise.intensity] > max_rank
            for day in training_days
            for exercise in day.exercises
        ):
            raise PlanGenerationError("模型生成的动作强度超过安全策略，请重试")
        if any(item not in available_evidence_ids for item in plan.evidence_ids):
            raise PlanGenerationError("模型引用了不存在的证据，请重试")

    def generate(self, db: DBSession, *, user_id: int, week_start: date) -> TrainingPlan:
        profile = self._load_profile(db, user_id)
        snapshot = load_fitness_snapshot(db, user_id=user_id)
        feedback = self._recent_feedback(db, user_id)
        safety = TrainingSafetyPolicy.assess(profile, snapshot, feedback)
        evidence_context, evidence_ids = self._retrieve_evidence(profile, safety)
        user_context = {
            "profile": self._profile_summary(profile),
            "fitness_snapshot": snapshot.to_prompt(),
            "recent_feedback": [
                {
                    "day_of_week": item.day_of_week,
                    "completed": item.completed,
                    "rpe": item.rpe,
                    "pain_score": item.pain_score,
                    "notes": item.notes,
                }
                for item in feedback
            ],
            "safety_policy": safety.to_dict(),
            "available_evidence_ids": evidence_ids,
            "retrieved_evidence": evidence_context,
        }
        response = self.model.invoke(
            [
                SystemMessage(content=load_training_plan_prompt()),
                HumanMessage(content=json.dumps(user_context, ensure_ascii=False)),
            ]
        )
        plan = WeeklyTrainingPlan.model_validate(
            _extract_json(getattr(response, "content", response))
        )
        self._validate_plan(
            plan,
            weekly_days=profile.weekly_days,
            safety=safety,
            available_evidence_ids=evidence_ids,
        )

        same_week_plans = (
            db.query(TrainingPlan)
            .filter(
                TrainingPlan.user_id == user_id,
                TrainingPlan.week_start == week_start,
            )
            .all()
        )
        next_version = max((item.version for item in same_week_plans), default=0) + 1
        for existing in same_week_plans:
            if existing.status != "active":
                continue
            existing.status = "archived"
        training_plan = TrainingPlan(
            id=uuid.uuid4().hex,
            user_id=user_id,
            week_start=week_start,
            version=next_version,
            status="active",
            plan_data=json.dumps(plan.model_dump(), ensure_ascii=False),
            safety_data=json.dumps(safety.to_dict(), ensure_ascii=False),
        )
        db.add(training_plan)
        db.flush()
        return training_plan

    @staticmethod
    def record_feedback(
        db: DBSession,
        *,
        plan: TrainingPlan,
        feedback: TrainingFeedbackCreate,
    ) -> TrainingFeedback:
        if feedback.day_of_week not in {
            day["day_of_week"] for day in _parse_json_field(plan.plan_data, {}).get("days", [])
        }:
            raise PlanGenerationError("反馈的星期不属于该训练计划")
        record = (
            db.query(TrainingFeedback)
            .filter(
                TrainingFeedback.plan_id == plan.id,
                TrainingFeedback.day_of_week == feedback.day_of_week,
            )
            .one_or_none()
        )
        if record is None:
            record = TrainingFeedback(plan_id=plan.id, day_of_week=feedback.day_of_week)
            db.add(record)
        record.completed = feedback.completed
        record.rpe = feedback.rpe
        record.pain_score = feedback.pain_score
        record.notes = feedback.notes
        db.flush()
        return record
