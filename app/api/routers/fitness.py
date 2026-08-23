import json
import hashlib
import re
from datetime import datetime, timedelta
from math import ceil
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.dialects.mysql import insert as mysql_upsert
from app.core.deps import get_db, get_coros
from app.models import User, FitnessData
from app.schemas import ApiResponse, FitnessDataResponse, FitnessSyncRequest, FitnessSyncResponse
from app.core.auth import get_current_user
from app.services.coros_client import CorosClient
from app.api.response import success_response

router = APIRouter(prefix="/api/fitness", tags=["fitness"])


# 先尝试插入。如果已经存在（user_id + date + data_type 三个字段重复了）
# ，那就只更新 data 字段的内容。
def _record_external_id(data_type: str, record: dict, date_str: str) -> str:
    """Return an idempotency key without collapsing separate same-day activities.

    Coros daily and sleep records are one-per-day snapshots. Activities instead prefer the
    upstream identifier and use a deterministic fingerprint only for older payloads that do
    not contain one.
    """

    if data_type in {"daily_metrics", "sleep"}:
        return f"{data_type}:{date_str}"
    for key in ("activity_id", "activityId", "id", "uuid"):
        if record.get(key) not in (None, ""):
            return f"activity:{record[key]}"
    stable_payload = {
        "start_time": record.get("start_time", ""),
        "name": record.get("name", ""),
        "sport_name": record.get("sport_name", ""),
        "duration_seconds": record.get("duration_seconds", ""),
        "distance_meters": record.get("distance_meters", ""),
    }
    digest = hashlib.sha256(
        json.dumps(stable_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return f"activity:fallback:{digest}"


def _upsert_fitness(
    db: Session,
    user_id: int,
    date_str: str,
    data_type: str,
    external_id: str,
    data: dict,
):
    stmt = (
        mysql_upsert(FitnessData)
        .values(
            user_id=user_id,
            date=datetime.strptime(date_str, "%Y%m%d").date(),
            data_type=data_type,
            external_id=external_id,
            data=json.dumps(data, ensure_ascii=False),
        )
        .on_duplicate_key_update(
            data=json.dumps(data, ensure_ascii=False),
        )
    )
    db.execute(stmt)


@router.post("/sync", response_model=ApiResponse[FitnessSyncResponse])
def sync_fitness(
    body: FitnessSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    coros: CorosClient = Depends(get_coros),
):
    today = datetime.now().strftime("%Y%m%d")
    default_start = (datetime.now() - timedelta(days=6)).strftime("%Y%m%d")
    start_day = body.start_day or default_start
    end_day = body.end_day or today
    start_date = datetime.strptime(start_day, "%Y%m%d")
    end_date = datetime.strptime(end_day, "%Y%m%d")
    requested_days = (end_date - start_date).days + 1
    weeks = max(1, ceil(requested_days / 7))

    upserted = 0
    unavailable_sources: list[str] = []

    """
    调 coros-mcp 拿到过去4周的每日指标（HRV、静息心率、训练负荷、VO2max等），
    遍历每条记录，在日期范围内的就调 _upsert_fitness 写入数据库。
    status_code=502 表示"上游服务错误"
    """
    try:
        cache_summary = coros.sync_cache(start_day, end_day)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"同步 Coros 缓存失败：{str(e)}")

    unavailable_sources.extend(cache_summary.get("failed_sources", []))

    try:
        records = coros.get_daily_metrics(weeks=weeks)
        for r in records:
            if start_day <= r["date"] <= end_day:
                _upsert_fitness(
                    db,
                    current_user.id,
                    r["date"],
                    "daily_metrics",
                    _record_external_id("daily_metrics", r, r["date"]),
                    r,
                )
                upserted += 1
    except Exception:
        unavailable_sources.append("daily")

    """
    睡眠数据（睡眠数据需要手机API）
    """
    cache_counts = cache_summary.get("cached_source_counts", {})
    sleep_unavailable = "sleep" in unavailable_sources and cache_counts.get("sleep", 0) == 0
    if not sleep_unavailable:
        try:
            records = coros.get_sleep_data(weeks=weeks)
            for r in records:
                if start_day <= r["date"] <= end_day:
                    _upsert_fitness(
                        db,
                        current_user.id,
                        r["date"],
                        "sleep",
                        _record_external_id("sleep", r, r["date"]),
                        r,
                    )
                    upserted += 1
        except Exception:
            unavailable_sources.append("sleep")
    else:
        # The provider's mobile sleep endpoint failed during the explicit cache refresh.
        # Do not issue a second upstream request from the read-only MCP process.
        unavailable_sources.append("sleep")

    """
    运动记录
    """
    try:
        result = coros.list_activities(start_day, end_day, size=100)
        for act in result.get("activities", []):
            start_time = act.get("start_time", "")
            digits = re.sub(r"\D", "", start_time)  # 去掉所有非数字字符
            act_date = digits[:8] if len(digits) >= 8 else ""
            if act_date:
                _upsert_fitness(
                    db,
                    current_user.id,
                    act_date,
                    "activity",
                    _record_external_id("activity", act, act_date),
                    act,
                )
                upserted += 1
    except Exception:
        unavailable_sources.append("activities")

    unavailable_sources = sorted(set(unavailable_sources))
    if upserted == 0 and unavailable_sources:
        raise HTTPException(status_code=502, detail="Coros 未返回可写入的运动数据")
    result = FitnessSyncResponse(
        upserted=upserted,
        partial=bool(unavailable_sources),
        unavailable_sources=unavailable_sources,
        cached_source_counts=cache_summary.get("cached_source_counts", {}),
    )
    messages = ["同步完成"] if not result.partial else ["部分同步完成，部分数据源暂不可用"]
    return success_response(result.model_dump(), messages=messages)


# 查当前用户近4周的每日指标
@router.get("/daily", response_model=ApiResponse[list[FitnessDataResponse]])
def get_daily_data(
    weeks: int = Query(default=4, ge=1, le=52),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now().date() - timedelta(weeks=weeks)
    records = (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == current_user.id,
            FitnessData.data_type == "daily_metrics",
            FitnessData.date >= since,
        )
        .order_by(FitnessData.date.desc())
        .all()
    )
    return success_response([FitnessDataResponse.model_validate(record) for record in records])


@router.get("/sleep", response_model=ApiResponse[list[FitnessDataResponse]])
def get_sleep_data(
    weeks: int = Query(default=4, ge=1, le=52),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now().date() - timedelta(weeks=weeks)
    records = (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == current_user.id,
            FitnessData.data_type == "sleep",
            FitnessData.date >= since,
        )
        .order_by(FitnessData.date.desc())
        .all()
    )
    return success_response([FitnessDataResponse.model_validate(record) for record in records])


@router.get("/activities", response_model=ApiResponse[list[FitnessDataResponse]])
def get_activities(
    start_day: str = Query(default="", pattern=r"^$|^\d{8}$"),
    end_day: str = Query(default="", pattern=r"^$|^\d{8}$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if start_day and end_day and start_day > end_day:
        raise HTTPException(status_code=422, detail="start_day 不能晚于 end_day")
    query = db.query(FitnessData).filter(
        FitnessData.user_id == current_user.id,
        FitnessData.data_type == "activity",
    )

    # 支持起止日期过滤
    if start_day:
        query = query.filter(FitnessData.date >= datetime.strptime(start_day, "%Y%m%d").date())
    if end_day:
        query = query.filter(FitnessData.date <= datetime.strptime(end_day, "%Y%m%d").date())
    records = query.order_by(FitnessData.date.desc()).all()
    return success_response([FitnessDataResponse.model_validate(record) for record in records])
