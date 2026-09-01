"""基于受约束生成和用户反馈的周训练计划 API。"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.response import success_response
from app.core.auth import get_current_user
from app.core.deps import get_db
from app.models import TrainingPlan, User
from app.schemas import (
    ApiResponse,
    TrainingFeedbackCreate,
    TrainingFeedbackResponse,
    TrainingPlanGenerateRequest,
    TrainingPlanResponse,
)
from app.services.training_plan_service import PlanGenerationError, TrainingPlanService
from app.utils.logger_handler import logger


router = APIRouter(prefix="/api/training-plans", tags=["training-plans"])
service = TrainingPlanService()


def _monday(value: date) -> date:
    """返回给定日期所在周的周一。"""
    return value - timedelta(days=value.weekday())


def _serialize(plan: TrainingPlan) -> TrainingPlanResponse:
    """映射持久化对象为公开 API 契约，避免暴露 ORM 字段名。"""

    return TrainingPlanResponse(
        id=plan.id,
        week_start=plan.week_start,
        version=plan.version,
        status=plan.status,
        plan=plan.plan_data,
        safety=plan.safety_data,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        feedbacks=[TrainingFeedbackResponse.model_validate(item) for item in plan.feedbacks],
    )


@router.get("/current", response_model=ApiResponse[TrainingPlanResponse | None])
def get_current_plan(
    week_start: date | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询当前用户指定周的最新有效训练计划。"""
    start = week_start or _monday(date.today())
    if start.weekday() != 0:
        raise HTTPException(status_code=422, detail="week_start 必须是周一")
    plan = (
        db.query(TrainingPlan)
        .filter(
            TrainingPlan.user_id == current_user.id,
            TrainingPlan.week_start == start,
            TrainingPlan.status == "active",
        )
        .order_by(TrainingPlan.version.desc())
        .first()
    )
    return success_response(_serialize(plan) if plan else None)


@router.post("/generate", response_model=ApiResponse[TrainingPlanResponse], status_code=201)
def generate_plan(
    body: TrainingPlanGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成当前用户指定周的安全训练计划并持久化。"""
    start = body.week_start or _monday(date.today())
    if start.weekday() != 0:
        raise HTTPException(status_code=422, detail="week_start 必须是周一")
    try:
        plan = service.generate(db, user_id=current_user.id, week_start=start)
        db.commit()
        db.refresh(plan)
    except PlanGenerationError as error:
        db.rollback()
        logger.info(
            "训练计划生成被业务校验拒绝：user_id=%s week_start=%s reason=%s",
            current_user.id,
            start.isoformat(),
            str(error),
        )
        raise HTTPException(status_code=422, detail=str(error)) from error
    return success_response(_serialize(plan), status_code=201)


@router.post("/{plan_id}/feedback", response_model=ApiResponse[TrainingFeedbackResponse])
def record_feedback(
    plan_id: str,
    body: TrainingFeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为当前用户的训练计划记录一条反馈。"""
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.id == plan_id, TrainingPlan.user_id == current_user.id)
        .one_or_none()
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="训练计划不存在")
    try:
        feedback = service.record_feedback(db, plan=plan, feedback=body)
        db.commit()
        db.refresh(feedback)
    except PlanGenerationError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return success_response(TrainingFeedbackResponse.model_validate(feedback))
