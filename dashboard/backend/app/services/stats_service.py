from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import datetime, timedelta, timezone

from ..models.stats import HourlyStats
from ..models.event import DetectionEvent
from ..models.device import Device, DeviceStatusCache
from ..utils.timeutil import KST, kst_day_bounds_utc, naive_utc_to_kst, utcnow

# 시간대 변환은 utils/timeutil.py 한 곳에서만 한다 — 같은 변환이 이 파일 안에만
# 세 벌 복붙돼 있었고, `datetime.utcnow()`(naive)와 KST 변환이 섞여 있었다.
def get_today_kst_bounds():
    return kst_day_bounds_utc()

def get_summary_stats(db: Session, date_str: str = None):
    # Determine bounds based on date_str (defaults to today)
    start_utc, end_utc = kst_day_bounds_utc(date_str)

    # Hourly stats aggregation for the period
    stats_agg = db.query(
        func.sum(HourlyStats.foot_traffic_count).label("total_foot_traffic"),
        func.sum(HourlyStats.cane_user_count).label("total_cane_users"),
        func.sum(HourlyStats.detection_count).label("total_detections")
    ).filter(
        HourlyStats.hour >= start_utc,
        HourlyStats.hour <= end_utc
    ).first()

    total_foot_traffic = stats_agg.total_foot_traffic or 0
    total_cane_users = stats_agg.total_cane_users or 0
    total_detections = stats_agg.total_detections or 0

    # Detection Event Average Confidence
    avg_conf = db.query(func.avg(DetectionEvent.confidence)).filter(
        DetectionEvent.timestamp >= start_utc,
        DetectionEvent.timestamp <= end_utc
    ).scalar()
    
    avg_conf = round(avg_conf, 3) if avg_conf else 0.0

    # Device Status Cache (Filtered by recently updated to exclude stale data)
    # Let's say updated within the last 5 minutes (300 seconds)
    five_mins_ago = utcnow() - timedelta(minutes=5)
    
    device_metrics = db.query(
        func.count(DeviceStatusCache.device_id).label("online_count"),
        func.avg(DeviceStatusCache.cpu_temp_c).label("avg_temp")
    ).filter(
        DeviceStatusCache.updated_at >= five_mins_ago
    ).first()
    
    online_device_count = device_metrics.online_count or 0
    avg_cpu_temperature = round(device_metrics.avg_temp, 1) if device_metrics.avg_temp else 0.0
    
    total_device_count = db.query(Device).count()
    online_rate = round(online_device_count / total_device_count, 3) if total_device_count > 0 else 0.0
    
    active_streams = online_device_count  # Mocked logic as per requirement
    total_streams = total_device_count    # Mocked logic
    active_alert_count = 0                # Mocked logic

    # Most active device
    most_active = db.query(
        HourlyStats.device_id,
        func.sum(HourlyStats.detection_count).label("cnt")
    ).filter(
        HourlyStats.hour >= start_utc,
        HourlyStats.hour <= end_utc
    ).group_by(HourlyStats.device_id).order_by(func.sum(HourlyStats.detection_count).desc()).first()

    most_active_device_data = None
    if most_active and most_active.cnt > 0:
        device_info = db.query(Device).filter(Device.id == most_active.device_id).first()
        if device_info:
            most_active_device_data = {
                "id": device_info.id,
                "name": device_info.name,
                "location": device_info.location,
                "today_detections": most_active.cnt
            }

    return {
        "total_foot_traffic_today": total_foot_traffic,
        "cane_user_count_today": total_cane_users,
        "total_detections_today": total_detections,
        "avg_confidence": avg_conf,
        "active_streams": active_streams,
        "total_streams": total_streams,
        "online_device_count": online_device_count,
        "total_device_count": total_device_count,
        "online_rate": online_rate,
        "active_alert_count": active_alert_count,
        "avg_cpu_temperature": avg_cpu_temperature,
        "most_active_device": most_active_device_data
    }

def get_device_stats(db: Session, date_str: str = None):
    start_utc, end_utc = kst_day_bounds_utc(date_str)

    results = db.query(
        HourlyStats.device_id,
        func.sum(HourlyStats.foot_traffic_count).label("ft"),
        func.sum(HourlyStats.cane_user_count).label("cu"),
        func.sum(HourlyStats.detection_count).label("det")
    ).filter(
        HourlyStats.hour >= start_utc,
        HourlyStats.hour <= end_utc
    ).group_by(HourlyStats.device_id).all()

    device_stats = []
    for r in results:
        device = db.query(Device).filter(Device.id == r.device_id).first()
        name = device.name if device else r.device_id
        device_stats.append({
            "device_id": r.device_id,
            "name": name,
            "foot_traffic": r.ft or 0,
            "cane_users": r.cu or 0,
            "detections": r.det or 0
        })

    return device_stats

def get_timeseries_stats(db: Session, device_id: str, period: str, granularity: str):
    now_kst = datetime.now(KST)
    padded_dict = {}

    if granularity == "hourly":
        # Create 24 buckets for today
        start_of_today_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_of_today_kst.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = (start_of_today_kst + timedelta(days=1) - timedelta(microseconds=1)).astimezone(timezone.utc).replace(tzinfo=None)
        
        for i in range(24):
            padded_dict[i] = {"hour": i, "total_count": 0, "cane_user_count": 0, "detections": 0}
            
        query = db.query(
            HourlyStats.hour,
            func.sum(HourlyStats.foot_traffic_count).label("ft"),
            func.sum(HourlyStats.cane_user_count).label("cu"),
            func.sum(HourlyStats.detection_count).label("det")
        ).filter(
            HourlyStats.hour >= start_utc,
            HourlyStats.hour <= end_utc
        )
        if device_id:
            query = query.filter(HourlyStats.device_id == device_id)
        
        db_results = query.group_by(HourlyStats.hour).all()
        
        for row in db_results:
            # hour is stored in naive UTC. Need to convert to KST hour
            kst_hour = naive_utc_to_kst(row.hour).hour
            
            if kst_hour in padded_dict:
                padded_dict[kst_hour]["total_count"] += (row.ft or 0)
                padded_dict[kst_hour]["cane_user_count"] += (row.cu or 0)
                padded_dict[kst_hour]["detections"] += (row.det or 0)

    else:
        # daily
        days = 7 if period == "7d" else 30
        start_date_kst = (now_kst - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_date_kst.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = now_kst.astimezone(timezone.utc).replace(tzinfo=None)
        
        for i in range(days):
            date_str = (start_date_kst + timedelta(days=i)).strftime("%Y-%m-%d")
            padded_dict[date_str] = {"date": date_str, "total_count": 0, "cane_user_count": 0, "detections": 0}

        # Instead of grouping by date in DB (which requires SQLite specific strftime),
        # we can fetch hourly stats and group them in Python for compatibility and precision.
        query = db.query(
            HourlyStats.hour,
            func.sum(HourlyStats.foot_traffic_count).label("ft"),
            func.sum(HourlyStats.cane_user_count).label("cu"),
            func.sum(HourlyStats.detection_count).label("det")
        ).filter(
            HourlyStats.hour >= start_utc,
            HourlyStats.hour <= end_utc
        )
        if device_id:
            query = query.filter(HourlyStats.device_id == device_id)
            
        db_results = query.group_by(HourlyStats.hour).all()
        
        for row in db_results:
            kst_date_str = naive_utc_to_kst(row.hour).strftime("%Y-%m-%d")
            
            if kst_date_str in padded_dict:
                padded_dict[kst_date_str]["total_count"] += (row.ft or 0)
                padded_dict[kst_date_str]["cane_user_count"] += (row.cu or 0)
                padded_dict[kst_date_str]["detections"] += (row.det or 0)

    return list(padded_dict.values())
