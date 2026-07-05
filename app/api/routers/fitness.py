import json
import re
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.dialects.mysql import insert as mysql_upsert
from app.core.deps import get_db, get_coros
from app.models import User, FitnessData
from app.schemas import FitnessSyncRequest, FitnessDataResponse
from app.core.auth import get_current_user
from app.services.coros_client import CorosClient

router = APIRouter(prefix="/api/fitness", tags=["fitness"])


# 先尝试插入。如果已经存在（user_id + date + data_type 三个字段重复了）
# ，那就只更新 data 字段的内容。
def _upsert_fitness(db: Session, user_id: int, date_str: str,
                    data_type: str, data: dict):
    stmt = mysql_upsert(FitnessData).values(
        user_id=user_id,
        date=datetime.strptime(date_str, "%Y%m%d").date(),
        data_type=data_type,
        data=json.dumps(data, ensure_ascii=False),
    ).on_duplicate_key_update(
        data=json.dumps(data, ensure_ascii=False),
    )
    db.execute(stmt)

@router.post("/sync")
def sync_fitness(
    body: FitnessSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    coros: CorosClient = Depends(get_coros),
):
    today = datetime.now().strftime("%Y%m%d")
    default_start = (datetime.now() - timedelta(weeks=4)).strftime("%Y%m%d")
    start_day = body.start_day or default_start
    end_day = body.end_day or today

    # 计数器，记录成功写入多少条数据
    upserted = 0

    """
    调 coros-mcp 拿到过去4周的每日指标（HRV、静息心率、训练负荷、VO2max等），
    遍历每条记录，在日期范围内的就调 _upsert_fitness 写入数据库。
    status_code=502 表示"上游服务错误"
    """
    try:
        records = coros.get_daily_metrics(weeks=4)
        for r in records:
            if start_day <= r["date"] <= end_day:
                _upsert_fitness(db, current_user.id, r["date"], "daily_metrics", r)
                upserted += 1
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"同步每日指标失败：{str(e)}")

    """
    睡眠数据（睡眠数据需要手机API）
    """
    try:
        records = coros.get_sleep_data(weeks=4)
        for r in records:
            if start_day <= r["date"] <= end_day:
                _upsert_fitness(db, current_user.id, r["date"], "sleep", r)
                upserted += 1
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"同步睡眠数据失败：{str(e)}")

    """
    运动记录
    """
    try:
        result = coros.list_activities(start_day, end_day, size=100)
        for act in result.get("activities", []):
            start_time = act.get("start_time", "")
            digits = re.sub(r"\D", "", start_time) # 去掉所有非数字字符
            act_date = digits[:8] if len(digits) >= 8 else ""
            if act_date:
                _upsert_fitness(db, current_user.id, act_date, "activity", act)
                upserted += 1
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"同步活动数据失败：{str(e)}")

    return {"message": "同步完成", "upserted": upserted}

# 查当前用户近4周的每日指标
@router.get("/daily", response_model=list[FitnessDataResponse])
def get_daily_data(
    weeks: int = 4,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now().date() - timedelta(weeks=weeks)
    return (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == current_user.id,
            FitnessData.data_type == "daily_metrics",
            FitnessData.date >= since,
        )
        .order_by(FitnessData.date.desc())
        .all()
    )

@router.get("/sleep", response_model=list[FitnessDataResponse])
def get_sleep_data(
    weeks: int = 4,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now().date() - timedelta(weeks=weeks)
    return (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == current_user.id,
            FitnessData.data_type == "sleep",
            FitnessData.date >= since,
        )
        .order_by(FitnessData.date.desc())
        .all()
    )

@router.get("/activities", response_model=list[FitnessDataResponse])
def get_activities(
    start_day: str = "",
    end_day: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(FitnessData).filter(
        FitnessData.user_id == current_user.id,
        FitnessData.data_type == "activity",
    )

    # 支持起止日期过滤
    if start_day:
        query = query.filter(FitnessData.date >= datetime.strptime(start_day, "%Y%m%d").date())
    if end_day:
        query = query.filter(FitnessData.date <= datetime.strptime(end_day, "%Y%m%d").date())
    return query.order_by(FitnessData.date.desc()).all()
