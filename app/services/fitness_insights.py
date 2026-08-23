"""Fitness-data aggregation that is independent of HTTP and Agent tools."""

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


@dataclass(frozen=True)
class FitnessSnapshot:
    """Bounded aggregates used by the Agent and plan safety policy, never raw Coros payloads."""

    days_observed: int = 0
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
        return self.days_observed > 0 or self.activity_count > 0

    def to_prompt(self) -> str:
        if not self.has_data:
            return "用户近4周暂无运动数据。"
        lines = ["用户近4周运动数据摘要："]
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


def load_fitness_snapshot(
    db: DBSession, *, user_id: int, weeks: int = 4, today: date | None = None
) -> FitnessSnapshot:
    """Read and aggregate recent fitness data in one service-layer operation."""

    since = (today or date.today()) - timedelta(weeks=weeks)
    records = (
        db.query(FitnessData)
        .filter(FitnessData.user_id == user_id, FitnessData.date >= since)
        .order_by(FitnessData.date.asc(), FitnessData.id.asc())
        .all()
    )
    daily_records: list[dict[str, Any]] = []
    sleep_records: list[dict[str, Any]] = []
    activities: list[dict[str, Any]] = []
    for record in records:
        try:
            value = json.loads(record.data) if isinstance(record.data, str) else record.data
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
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
        sport = str(activity.get("name") or activity.get("sport_name") or "未知运动")
        sport_counts[sport] = sport_counts.get(sport, 0) + 1
        duration = activity.get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            activity_seconds += duration

    vo2max_values = _numeric(daily_records, "vo2max")
    return FitnessSnapshot(
        days_observed=len(daily_records),
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
