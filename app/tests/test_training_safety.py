"""LLM 训练计划生成前确定性安全门的单元测试。"""

import pytest

from app.models import UserProfile
from app.schemas import PlanDay, PlanExercise, WeeklyTrainingPlan
from app.services.fitness_insights import FitnessSnapshot
from app.services.training_plan_service import (
    PlanGenerationError,
    TrainingPlanService,
    TrainingSafetyPolicy,
)


def _week_plan(
    *, intensity: str = "低", evidence_ids: list[str] | None = None
) -> WeeklyTrainingPlan:
    """构造指定训练强度和证据标识的七天周计划。"""
    days = []
    for day_of_week in range(1, 8):
        if day_of_week in {1, 3, 5}:
            days.append(
                PlanDay(
                    day_of_week=day_of_week,
                    title="基础力量",
                    focus="动作质量",
                    kind="训练",
                    exercises=[
                        PlanExercise(
                            name="徒手深蹲",
                            sets=3,
                            reps="8-10",
                            intensity=intensity,
                        )
                    ],
                )
            )
        else:
            days.append(
                PlanDay(
                    day_of_week=day_of_week,
                    title="恢复",
                    focus="睡眠与步行",
                    kind="恢复",
                )
            )
    return WeeklyTrainingPlan(
        title="本周计划", goal="健康", days=days, evidence_ids=evidence_ids or []
    )


def test_safety_policy_downgrades_for_injury_load_sleep_and_pain():
    """验证伤病、高负荷、睡眠不足和疼痛会降低安全强度上限。"""
    profile = UserProfile(experience="advanced", injuries='["膝盖"]')
    snapshot = FitnessSnapshot(max_training_load_ratio=1.4, avg_sleep_hours=5.5)
    feedback = [type("Feedback", (), {"pain_score": 5, "rpe": 8})()]

    safety = TrainingSafetyPolicy.assess(profile, snapshot, feedback)

    assert safety.maximum_intensity == "低"
    assert len(safety.signals) >= 2
    assert safety.constraints


def test_plan_validator_rejects_intensity_above_deterministic_safety_limit():
    """验证计划验证器拒绝超过确定性安全上限的训练强度。"""
    profile = UserProfile(experience="beginner", injuries='["腰部不适"]')
    safety = TrainingSafetyPolicy.assess(profile, FitnessSnapshot(), [])

    with pytest.raises(PlanGenerationError, match="强度"):
        TrainingPlanService._validate_plan(
            _week_plan(intensity="中"),
            weekly_days=3,
            safety=safety,
            available_evidence_ids=[],
        )


def test_plan_validator_rejects_hallucinated_evidence_id():
    """验证计划验证器拒绝检索结果中不存在的证据标识。"""
    safety = TrainingSafetyPolicy.assess(UserProfile(), FitnessSnapshot(), [])

    with pytest.raises(PlanGenerationError, match="证据"):
        TrainingPlanService._validate_plan(
            _week_plan(evidence_ids=["invented-source#1"]),
            weekly_days=3,
            safety=safety,
            available_evidence_ids=["known-source#1"],
        )
