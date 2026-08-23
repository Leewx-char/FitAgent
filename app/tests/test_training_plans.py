"""API-level test for structured plan persistence without a real LLM or Qdrant."""

import json
from types import SimpleNamespace

from app.api.routers import training_plans
from app.services.training_plan_service import TrainingPlanService


def _model_plan() -> dict:
    days = []
    for day_of_week in range(1, 8):
        if day_of_week in {1, 3, 5}:
            days.append(
                {
                    "day_of_week": day_of_week,
                    "title": "基础力量",
                    "focus": "动作质量",
                    "kind": "训练",
                    "exercises": [
                        {
                            "name": "徒手深蹲",
                            "sets": 3,
                            "reps": "8-10",
                            "intensity": "低",
                            "notes": "无痛范围内完成",
                        }
                    ],
                }
            )
        else:
            days.append(
                {
                    "day_of_week": day_of_week,
                    "title": "恢复",
                    "focus": "步行与睡眠",
                    "kind": "恢复",
                    "exercises": [],
                }
            )
    return {
        "title": "本周基础计划",
        "goal": "健康管理",
        "days": days,
        "rationale": ["逐步建立训练习惯"],
        "safety_notes": ["疼痛加重时停止训练"],
        "evidence_ids": [],
    }


class FakePlanModel:
    def invoke(self, messages):
        return SimpleNamespace(content=json.dumps(_model_plan(), ensure_ascii=False))


class FakeRagService:
    def build_context(self, query):
        return SimpleNamespace(content="训练与恢复证据", result=SimpleNamespace(hits=[]))


def test_generate_plan_and_upsert_feedback(auth_client, monkeypatch):
    profile = auth_client.post(
        "/api/profile",
        json={
            "gender": "male",
            "age": 28,
            "height": 175,
            "weight": 70,
            "goal": "health",
            "weekly_days": 3,
            "experience": "beginner",
            "injuries": [],
        },
    )
    assert profile.status_code == 201
    monkeypatch.setattr(
        training_plans,
        "service",
        TrainingPlanService(model=FakePlanModel(), rag_service=FakeRagService()),
    )

    generated = auth_client.post("/api/training-plans/generate", json={})

    assert generated.status_code == 201
    plan = generated.json()["data"]
    assert plan["status"] == "active"
    assert len(plan["plan"]["days"]) == 7
    assert plan["safety"]["maximum_intensity"] == "中"

    feedback_payload = {"day_of_week": 1, "completed": True, "rpe": 6, "pain_score": 0}
    first = auth_client.post(f"/api/training-plans/{plan['id']}/feedback", json=feedback_payload)
    feedback_payload["rpe"] = 7
    second = auth_client.post(f"/api/training-plans/{plan['id']}/feedback", json=feedback_payload)

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert second.json()["data"]["rpe"] == 7
