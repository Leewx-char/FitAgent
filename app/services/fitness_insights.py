"""与 HTTP 和 Agent 工具无关的运动数据受限聚合服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session as DBSession

from app.models import FitnessData


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _numeric(records: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _record_payload(record: FitnessData) -> dict[str, Any] | None:
    """安全解析单条设备记录；坏 JSON 不能中断整段数据的分析。"""

    try:
        value = json.loads(record.data) if isinstance(record.data, str) else record.data
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _activity_name(payload: dict[str, Any]) -> str:
    return str(payload.get("name") or payload.get("sport_name") or "未知运动")


def _activity_duration_minutes(payload: dict[str, Any]) -> int | None:
    duration = payload.get("duration_seconds")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return round(float(duration) / 60)
    return None


def _first_numeric(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


@dataclass(frozen=True)
class FitnessSnapshot:
    """Agent 与训练计划共用的受限聚合快照，绝不包含原始 Coros 载荷。"""

    period_label: str = "近4周"
    days_observed: int = 0
    sleep_days: int = 0
    activity_count: int = 0
    activity_duration_minutes: int = 0
    sport_counts: dict[str, int] = field(default_factory=dict)
    avg_rhr: float | None = None
    avg_hrv: float | None = None
    avg_training_load: float | None = None
    max_training_load_ratio: float | None = None
    avg_tired_rate: float | None = None
    latest_vo2max: float | None = None
    avg_sleep_hours: float | None = None
    avg_deep_sleep_minutes: float | None = None

    @property
    def has_data(self) -> bool:
        return self.days_observed > 0 or self.sleep_days > 0 or self.activity_count > 0

    def to_prompt(self) -> str:
        if not self.has_data:
            return (
                f"用户{self.period_label}暂无运动数据。请引导用户去 Dashboard 点击「同步」按钮获取"
                "高驰设备数据，同步后即可基于真实运动数据提供个性化建议。"
            )
        lines = [f"用户{self.period_label}运动数据摘要："]
        if self.days_observed:
            lines.append(f"- 有效日指标数据：{self.days_observed}天")
        if self.avg_rhr is not None:
            label = (
                "偏低，恢复良好"
                if self.avg_rhr < 60
                else "偏高，注意恢复"
                if self.avg_rhr > 75
                else "正常"
            )
            lines.append(f"- 平均静息心率：{self.avg_rhr:.0f} bpm（{label}）")
        if self.avg_hrv is not None:
            lines.append(f"- 平均睡眠HRV(RMSSD)：{self.avg_hrv:.0f} ms")
        if self.avg_training_load is not None:
            lines.append(f"- 日均训练负荷：{self.avg_training_load:.0f}")
        if self.max_training_load_ratio is not None:
            label = (
                "急性负荷偏高，注意恢复" if self.max_training_load_ratio > 1.3 else "负荷比例正常"
            )
            lines.append(
                f"- 最高训练负荷比(急性/慢性)：{self.max_training_load_ratio:.1f}（{label}）"
            )
        if self.avg_tired_rate is not None:
            lines.append(f"- 平均疲劳度：{self.avg_tired_rate:.1f}")
        if self.latest_vo2max is not None:
            lines.append(f"- 最新VO2max：{self.latest_vo2max:.0f}")
        if self.avg_sleep_hours is not None:
            lines.append(f"- 平均睡眠时长：{self.avg_sleep_hours:.1f}小时")
        if self.avg_deep_sleep_minutes is not None:
            lines.append(f"- 平均深度睡眠：{self.avg_deep_sleep_minutes:.0f}分钟")
        if self.activity_count:
            lines.append(f"- 运动次数：{self.activity_count}次")
            sports = sorted(self.sport_counts.items(), key=lambda item: -item[1])[:5]
            lines.append(
                f"- 运动类型分布：{'、'.join(f'{name}x{count}' for name, count in sports)}"
            )
            if self.activity_duration_minutes:
                lines.append(f"- 总运动时长：{self.activity_duration_minutes / 60:.1f}小时")
        return "\n".join(lines)


@dataclass(frozen=True)
class ActivityCandidate:
    """单日候选活动；只暴露定位单次活动所需的最小字段。"""

    external_id: str
    start_time: str
    name: str
    duration_minutes: int | None

    def to_prompt(self) -> str:
        duration = (
            f"，时长约{self.duration_minutes}分钟" if self.duration_minutes is not None else ""
        )
        return (
            f"- 活动ID={self.external_id} | 开始={self.start_time or '未记录'} | "
            f"{self.name}{duration}"
        )


@dataclass(frozen=True)
class ActivitySnapshot:
    """一条由稳定 external_id 定位的活动白名单摘要。"""

    external_id: str
    start_time: str
    name: str
    duration_minutes: int | None
    distance_meters: float | None
    avg_heart_rate: float | None
    max_heart_rate: float | None
    training_load: float | None
    calories: float | None

    def to_prompt(self) -> str:
        lines = [
            "单次活动摘要：",
            f"- 活动ID：{self.external_id}",
            f"- 类型：{self.name}",
            f"- 开始时间：{self.start_time or '未记录'}",
        ]
        if self.duration_minutes is not None:
            lines.append(f"- 时长：约{self.duration_minutes}分钟")
        if self.distance_meters is not None:
            lines.append(f"- 距离：{self.distance_meters / 1000:.2f}公里")
        if self.avg_heart_rate is not None:
            lines.append(f"- 平均心率：{self.avg_heart_rate:.0f} bpm")
        if self.max_heart_rate is not None:
            lines.append(f"- 最大心率：{self.max_heart_rate:.0f} bpm")
        if self.training_load is not None:
            lines.append(f"- 训练负荷：{self.training_load:.0f}")
        if self.calories is not None:
            lines.append(f"- 消耗热量：{self.calories:.0f} kcal")
        return "\n".join(lines)


def _resolve_period(
    *,
    start_date: date | None,
    end_date: date | None,
    weeks: int,
    today: date | None,
) -> tuple[date, date, str]:
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date 和 end_date 必须同时提供")
    if start_date is not None and end_date is not None:
        if start_date > end_date:
            raise ValueError("start_date 不能晚于 end_date")
        return start_date, end_date, f"{start_date.isoformat()} 至 {end_date.isoformat()}"
    if weeks < 1:
        raise ValueError("weeks 必须大于 0")
    resolved_end = today or date.today()
    return resolved_end - timedelta(weeks=weeks), resolved_end, f"近{weeks}周"


def load_fitness_snapshot(
    db: DBSession,
    *,
    user_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    weeks: int = 4,
    today: date | None = None,
) -> FitnessSnapshot:
    """按用户和受限日期区间读取并聚合设备数据。"""

    start_date, end_date, period_label = _resolve_period(
        start_date=start_date,
        end_date=end_date,
        weeks=weeks,
        today=today,
    )
    records = (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == user_id,
            FitnessData.date >= start_date,
            FitnessData.date <= end_date,
        )
        .order_by(FitnessData.date.asc(), FitnessData.id.asc())
        .all()
    )
    daily_records: list[dict[str, Any]] = []
    sleep_records: list[dict[str, Any]] = []
    activities: list[dict[str, Any]] = []
    for record in records:
        value = _record_payload(record)
        if value is None:
            continue
        if record.data_type == "daily_metrics":
            daily_records.append(value)
        elif record.data_type == "sleep":
            sleep_records.append(value)
        elif record.data_type == "activity":
            activities.append(value)

    durations = _numeric(sleep_records, "total_duration_minutes")
    deep_sleep = [
        float(item["phases"]["deep_minutes"])
        for item in sleep_records
        if isinstance(item.get("phases"), dict)
        and isinstance(item["phases"].get("deep_minutes"), (int, float))
    ]
    sport_counts: dict[str, int] = {}
    activity_seconds = 0.0
    for activity in activities:
        sport = _activity_name(activity)
        sport_counts[sport] = sport_counts.get(sport, 0) + 1
        duration = activity.get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            activity_seconds += duration

    vo2max_values = _numeric(daily_records, "vo2max")
    return FitnessSnapshot(
        period_label=period_label,
        days_observed=len(daily_records),
        sleep_days=len(sleep_records),
        activity_count=len(activities),
        activity_duration_minutes=round(activity_seconds / 60),
        sport_counts=sport_counts,
        avg_rhr=_mean(_numeric(daily_records, "rhr")),
        avg_hrv=_mean(_numeric(daily_records, "avg_sleep_hrv")),
        avg_training_load=_mean(_numeric(daily_records, "training_load")),
        max_training_load_ratio=max(_numeric(daily_records, "training_load_ratio"), default=None),
        avg_tired_rate=_mean(_numeric(daily_records, "tired_rate")),
        latest_vo2max=vo2max_values[-1] if vo2max_values else None,
        avg_sleep_hours=(_mean(durations) / 60) if durations else None,
        avg_deep_sleep_minutes=_mean(deep_sleep),
    )


def list_activity_candidates(
    db: DBSession, *, user_id: int, activity_date: date
) -> list[ActivityCandidate]:
    """列出某一天的活动候选，供 Agent 选择稳定 external_id，而非猜测记录。"""

    records = (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == user_id,
            FitnessData.date == activity_date,
            FitnessData.data_type == "activity",
        )
        .order_by(FitnessData.id.asc())
        .all()
    )
    candidates = []
    for record in records:
        payload = _record_payload(record)
        if payload is None:
            continue
        candidates.append(
            ActivityCandidate(
                external_id=record.external_id,
                start_time=str(payload.get("start_time") or ""),
                name=_activity_name(payload),
                duration_minutes=_activity_duration_minutes(payload),
            )
        )
    return sorted(candidates, key=lambda item: (item.start_time, item.external_id))


def load_activity_snapshot(
    db: DBSession, *, user_id: int, activity_date: date, external_id: str
) -> ActivitySnapshot | None:
    """以用户、日期和稳定 external_id 精确读取一项活动的白名单指标。"""

    record = (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == user_id,
            FitnessData.date == activity_date,
            FitnessData.data_type == "activity",
            FitnessData.external_id == external_id,
        )
        .one_or_none()
    )
    if record is None:
        return None
    payload = _record_payload(record)
    if payload is None:
        return None
    return ActivitySnapshot(
        external_id=record.external_id,
        start_time=str(payload.get("start_time") or ""),
        name=_activity_name(payload),
        duration_minutes=_activity_duration_minutes(payload),
        distance_meters=_first_numeric(payload, "distance_meters", "distance"),
        avg_heart_rate=_first_numeric(payload, "avg_heart_rate", "average_heart_rate"),
        max_heart_rate=_first_numeric(payload, "max_heart_rate", "maximum_heart_rate"),
        training_load=_first_numeric(payload, "training_load"),
        calories=_first_numeric(payload, "calories", "calorie"),
    )
