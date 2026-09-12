"""实时 COROS 运动快照接口，不将运动数据写入 MySQL。"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.response import success_response
from app.core.auth import get_current_user
from app.core.deps import get_db
from app.models import User
from app.schemas import ApiResponse, FitnessSnapshotResponse
from app.services.coros_live_gateway import CorosMcpError, get_coros_live_gateway
from app.services.coros_oauth import (
    CorosNotConnectedError,
    CorosOAuthError,
    CorosReconnectionRequiredError,
)

router = APIRouter(prefix="/api/fitness", tags=["fitness"])


@router.get("/snapshot", response_model=ApiResponse[FitnessSnapshotResponse])
def get_fitness_snapshot(
    weeks: int = Query(default=4, ge=1, le=13),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取 Dashboard 所需的实时日健康、睡眠和活动白名单快照。"""

    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks)
    try:
        live_data = get_coros_live_gateway().fetch_snapshot(
            db,
            user_id=current_user.id,
            start_date=start_date,
            end_date=end_date,
        )
    except CorosNotConnectedError as error:
        raise HTTPException(status_code=409, detail="尚未连接 COROS") from error
    except CorosReconnectionRequiredError as error:
        raise HTTPException(status_code=409, detail="COROS 授权已失效，请重新连接") from error
    except (CorosMcpError, CorosOAuthError) as error:
        raise HTTPException(status_code=502, detail="COROS 实时数据暂不可用") from error

    return success_response(
        FitnessSnapshotResponse(
            start_date=live_data.start_date.isoformat(),
            end_date=live_data.end_date.isoformat(),
            daily_metrics=live_data.daily_metrics,
            sleep_records=live_data.sleep_records,
            activities=live_data.activities,
            partial=live_data.partial,
            unavailable_sources=live_data.unavailable_sources,
        )
    )
