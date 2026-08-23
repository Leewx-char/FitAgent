import json
from datetime import date, timedelta

from app.core.database import SessionLocal
from app.services.agent_tools import _user_context, get_fitness_summary
from app.models import FitnessData


class TestFitnessSummary:
    def test_no_user_context(self):
        """无 user_id → 提示登录"""
        _user_context.set({})
        result = get_fitness_summary.invoke({})
        assert "未获取到用户信息" in result

    def test_no_data(self, auth_client):
        """有 user_id 但无运动数据 → 引导同步"""
        me = auth_client.get("/api/auth/me").json()
        _user_context.set({"user_id": me["data"]["id"]})
        result = get_fitness_summary.invoke({})
        assert "暂无运动数据" in result or "引导" in result

    def test_daily_metrics_summary(self, auth_client):
        """有 daily_metrics → 摘要含静息心率、HRV、训练负荷、VO2max"""
        me = auth_client.get("/api/auth/me").json()
        user_id = me["data"]["id"]
        _user_context.set({"user_id": user_id})

        db = SessionLocal()
        try:
            today = date.today()
            db.add(
                FitnessData(
                    user_id=user_id,
                    date=today - timedelta(days=1),
                    data_type="daily_metrics",
                    external_id="test:summary:daily:1",
                    data=json.dumps(
                        {
                            "rhr": 52,
                            "avg_sleep_hrv": 72,
                            "training_load": 45,
                            "vo2max": 48,
                            "training_load_ratio": 1.1,
                            "tired_rate": 2.5,
                        }
                    ),
                )
            )
            db.add(
                FitnessData(
                    user_id=user_id,
                    date=today - timedelta(days=2),
                    data_type="daily_metrics",
                    external_id="test:summary:daily:2",
                    data=json.dumps(
                        {
                            "rhr": 54,
                            "avg_sleep_hrv": 68,
                            "training_load": 55,
                            "vo2max": 47,
                            "training_load_ratio": 1.2,
                            "tired_rate": 3.0,
                        }
                    ),
                )
            )
            db.commit()

            result = get_fitness_summary.invoke({})

            assert "有效日指标数据" in result
            assert "静息心率" in result
            assert "HRV" in result
            assert "训练负荷" in result
            assert "VO2max" in result
            assert "疲劳度" in result
            # 清理
            db.query(FitnessData).filter(FitnessData.user_id == user_id).delete()
            db.commit()
        finally:
            db.close()

    def test_all_data_types(self, auth_client):
        """三种数据类型齐全 → 日指标+睡眠+运动三段都有"""
        me = auth_client.get("/api/auth/me").json()
        user_id = me["data"]["id"]
        _user_context.set({"user_id": user_id})

        db = SessionLocal()
        try:
            today = date.today()
            db.add(
                FitnessData(
                    user_id=user_id,
                    date=today - timedelta(days=1),
                    data_type="daily_metrics",
                    external_id="test:all:daily",
                    data=json.dumps({"rhr": 60, "avg_sleep_hrv": 55}),
                )
            )
            db.add(
                FitnessData(
                    user_id=user_id,
                    date=today - timedelta(days=1),
                    data_type="sleep",
                    external_id="test:all:sleep",
                    data=json.dumps(
                        {"total_duration_minutes": 450, "phases": {"deep_minutes": 100}}
                    ),
                )
            )
            db.add(
                FitnessData(
                    user_id=user_id,
                    date=today - timedelta(days=2),
                    data_type="activity",
                    external_id="test:all:activity",
                    data=json.dumps(
                        {
                            "name": "跑步",
                            "duration_seconds": 2400,
                            "sport_name": "跑步",
                        }
                    ),
                )
            )
            db.commit()

            result = get_fitness_summary.invoke({})

            assert "睡眠时长" in result
            assert "深度睡眠" in result
            assert "运动次数" in result
            assert "跑步" in result
            # 清理
            db.query(FitnessData).filter(FitnessData.user_id == user_id).delete()
            db.commit()
        finally:
            db.close()

    def test_rhr_label(self, auth_client):
        """低静息心率(<60) → 标注'偏低，恢复良好'"""
        me = auth_client.get("/api/auth/me").json()
        user_id = me["data"]["id"]
        _user_context.set({"user_id": user_id})

        db = SessionLocal()
        try:
            db.add(
                FitnessData(
                    user_id=user_id,
                    date=date.today() - timedelta(days=1),
                    data_type="daily_metrics",
                    external_id="test:rhr:daily",
                    data=json.dumps({"rhr": 48}),
                )
            )
            db.commit()

            result = get_fitness_summary.invoke({})
            assert "偏低，恢复良好" in result
            # 清理
            db.query(FitnessData).filter(FitnessData.user_id == user_id).delete()
            db.commit()
        finally:
            db.close()
