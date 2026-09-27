from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from enum import Enum

from ..database import get_db
from ..deps import get_current_user
from ..schemas.stats import StatsSummaryResponse, DeviceStatsResponse, TimeSeriesResponse
from ..services import stats_service

router = APIRouter(prefix="/api/stats", tags=["Stats"])

class PeriodEnum(str, Enum):
    today = "today"
    seven_days = "7d"
    thirty_days = "30d"

class GranularityEnum(str, Enum):
    hourly = "hourly"
    daily = "daily"

@router.get("/summary", response_model=StatsSummaryResponse)
async def get_summary(
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    data = await stats_service.get_summary_stats(db, date)
    return {"data": data, "ok": True}

@router.get("/devices", response_model=DeviceStatsResponse)
def get_devices(
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    data = stats_service.get_device_stats(db, date)
    return {"data": data, "ok": True}

@router.get("/timeseries", response_model=TimeSeriesResponse)
def get_timeseries(
    period: PeriodEnum,
    granularity: Optional[GranularityEnum] = None,
    device_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # 기본값은 period가 정한다. 예전에는 hourly로 고정돼 있어서 `?period=7d`만 주면
    # "hourly는 today에만 유효" 400이 났다 — 통계 화면의 기본 호출이 그 형태다.
    if granularity is None:
        granularity = (GranularityEnum.hourly if period == PeriodEnum.today
                       else GranularityEnum.daily)
    if granularity == GranularityEnum.hourly and period != PeriodEnum.today:
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR",
                    "message": "hourly 집계는 period=today에서만 쓸 수 있습니다"},
        )
        
    data = stats_service.get_timeseries_stats(db, device_id, period.value, granularity.value)
    
    return {
        "data": data,
        "period": period.value,
        "granularity": granularity.value,
        "ok": True
    }
