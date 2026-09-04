import json
from datetime import date, timedelta
from types import SimpleNamespace

from langchain.tools import ToolRuntime

from app.core.database import SessionLocal
from app.services.agent_tools import get_fitness_summary
from app.services.chat_routing_graph import ChatRuntimeContext
from app.models import FitnessData


def _fitness_summary(user_id: int | None, **tool_args: str) -> str:
    """使用请求级 ToolRuntime 调用运动摘要工具。"""
    runtime = ToolRuntime(
        state={},
        context=ChatRuntimeContext(
            user_id=user_id or 0,
            city="",
            session_id="fitness-summary-test",
            dependencies=SimpleNamespace(),
        ),
        config={},
        stream_writer=lambda _event: None,
        tool_call_id="fitness-summary-test",
        store=None,
    )
    return get_fitness_summary.func(runtime=runtime, **tool_args)


class TestFitnessSummary:
    def test_missing_user_id(self):
        """无 user_id → 提示登录"""
        result = _fitness_summary(None)
        assert "未获取到用户信息" in result

    def test_no_data(self, auth_client):
        """有 user_id 但无运动数据 → 引导同步"""
        me = auth_client.get("/api/auth/me").json()
        result = _fitness_summary(me["data"]["id"])
        assert "暂无运动数据" in result or "引导" in result

    def test_daily_metrics_summary(self, auth_client):
        """有 daily_metrics → 摘要含静息心率、HRV、训练负荷、VO2max"""
        me = auth_client.get("/api/auth/me").json()
        user_id = me["data"]["id"]

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

            result = _fitness_summary(user_id)

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

            result = _fitness_summary(user_id)

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

            result = _fitness_summary(user_id)
            assert "偏低，恢复良好" in result
            # 清理
            db.query(FitnessData).filter(FitnessData.user_id == user_id).delete()
            db.commit()
        finally:
            db.close()

    def test_interval_summary_uses_only_requested_days(self, auth_client):
        """显式日期区间不能混入区间外的同一用户数据。"""
        me = auth_client.get("/api/auth/me").json()
        user_id = me["data"]["id"]
        start_day = date.today() - timedelta(days=4)
        end_day = date.today() - timedelta(days=3)
        outside_day = date.today() - timedelta(days=10)

        db = SessionLocal()
        try:
            db.add_all(
                [
                    FitnessData(
                        user_id=user_id,
                        date=start_day,
                        data_type="daily_metrics",
                        external_id="test:interval:inside:1",
                        data=json.dumps({"rhr": 58}),
                    ),
                    FitnessData(
                        user_id=user_id,
                        date=end_day,
                        data_type="daily_metrics",
                        external_id="test:interval:inside:2",
                        data=json.dumps({"rhr": 60}),
                    ),
                    FitnessData(
                        user_id=user_id,
                        date=outside_day,
                        data_type="daily_metrics",
                        external_id="test:interval:outside",
                        data=json.dumps({"rhr": 80}),
                    ),
                ]
            )
            db.commit()

            result = _fitness_summary(
                user_id,
                start_day=start_day.strftime("%Y%m%d"),
                end_day=end_day.strftime("%Y%m%d"),
            )

            assert f"{start_day.isoformat()} 至 {end_day.isoformat()}" in result
            assert "有效日指标数据：2天" in result
            assert "59 bpm" in result
            assert "80 bpm" not in result
        finally:
            db.query(FitnessData).filter(FitnessData.user_id == user_id).delete()
            db.commit()
            db.close()

    def test_same_day_activity_candidates_can_be_selected_by_external_id(self, auth_client):
        """同日多次活动先列候选，再用稳定 ID 精确读取其中一次。"""
        me = auth_client.get("/api/auth/me").json()
        user_id = me["data"]["id"]
        activity_day = date.today() - timedelta(days=1)
        morning_id = "activity:test-morning"
        evening_id = "activity:test-evening"

        db = SessionLocal()
        try:
            db.add_all(
                [
                    FitnessData(
                        user_id=user_id,
                        date=activity_day,
                        data_type="activity",
                        external_id=morning_id,
                        data=json.dumps(
                            {
                                "name": "晨跑",
                                "start_time": f"{activity_day:%Y%m%d}T070000",
                                "duration_seconds": 1800,
                            }
                        ),
                    ),
                    FitnessData(
                        user_id=user_id,
                        date=activity_day,
                        data_type="activity",
                        external_id=evening_id,
                        data=json.dumps(
                            {
                                "name": "夜跑",
                                "start_time": f"{activity_day:%Y%m%d}T190000",
                                "duration_seconds": 2400,
                                "distance_meters": 6000,
                                "avg_heart_rate": 145,
                                "training_load": 70,
                            }
                        ),
                    ),
                ]
            )
            db.commit()
            day = activity_day.strftime("%Y%m%d")

            candidates = _fitness_summary(user_id, start_day=day, end_day=day)
            detail = _fitness_summary(
                user_id, start_day=day, end_day=day, activity_id=evening_id
            )

            assert morning_id in candidates
            assert evening_id in candidates
            assert "晨跑" in candidates
            assert "夜跑" in candidates
            assert "单次活动摘要" in detail
            assert "夜跑" in detail
            assert "6.00公里" in detail
            assert "晨跑" not in detail
        finally:
            db.query(FitnessData).filter(FitnessData.user_id == user_id).delete()
            db.commit()
            db.close()

    def test_interval_validation_rejects_partial_dates_and_wrong_activity_scope(self, auth_client):
        """模型不能用半个日期区间或跨天活动 ID 绕过查询边界。"""
        me = auth_client.get("/api/auth/me").json()
        user_id = me["data"]["id"]
        day = (date.today() - timedelta(days=1)).strftime("%Y%m%d")

        partial = _fitness_summary(user_id, start_day=day)
        cross_day = _fitness_summary(
            user_id,
            start_day=(date.today() - timedelta(days=2)).strftime("%Y%m%d"),
            end_day=day,
            activity_id="activity:not-allowed",
        )

        assert "必须同时提供" in partial
        assert "只能用于" in cross_day
