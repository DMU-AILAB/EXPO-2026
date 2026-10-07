import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
import json

from ..database import SessionLocal, engine
from ..models.schedule import ScheduledReboot
from ..models.device import Device
from .pi_client import PiClient
from ..models.calibration import CalibrationRun, CalibrationSchedule
from .calibration_service import execute_schedule as execute_calibration

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def execute_reboot(device_id: str):
    """지정된 디바이스에 재부팅 명령을 보냅니다. 실패 시 로깅만 하고 무시합니다 (Fire-and-forget)."""
    db: Session = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            logger.warning(f"Reboot job cancelled: device {device_id} not found.")
            return

        logger.info(f"Executing scheduled reboot for {device_id} at {device.ip}")
        if not device.control_key:
            logger.warning("Scheduled reboot skipped for %s: device is not provisioned", device_id)
            return

        # Pi의 실제 제어 계약을 사용한다. 예전 /reboot 경로는 존재하지 않아
        # 예약 재부팅만 조용히 실패하고 있었다.
        await PiClient(device.ip, timeout=3.0).post_control(
            "/api/system/reboot", device.control_key, params={"confirm": "true"},
        )

    except Exception as e:
        # 스케줄 미스(기기 오프라인 등)는 다음 예약을 막지 않는다.
        logger.warning(f"Scheduled reboot failed for {device_id} ({e}). Skipping to next cycle.")
    finally:
        db.close()

def to_apscheduler_dow(days: list[int]) -> str:
    """명세의 요일 번호(0=일 … 6=토)를 APScheduler의 것(0=월 … 6=일)으로 옮긴다.

    **두 체계가 하루 어긋난다.** 변환 없이 넘기면 "월·수·금 03:00"으로 등록한 예약이
    화·목·토에 실행된다(실측: `[1,3,5]` → Tue/Thu/Sat). 재부팅은 조용히 하루 밀려도
    눈에 잘 띄지 않아서 현장에서 오래 남는 종류의 버그다.
    """
    return ",".join(str((d - 1) % 7) for d in sorted(set(days)))


def add_schedule_job(schedule_id: int, device_id: str, days: list[int], hour: int):
    """새로운 예약을 스케줄러에 등록합니다. Step 4 방어: schedule_id 명시적 바인딩"""
    day_of_week = to_apscheduler_dow(days)   # 명세 0=일 → APScheduler 0=월

    # job_id를 schedule_id 문자열로 고정하여 추후 제어 가능하게 함
    job_id = str(schedule_id)
    
    # 이미 존재하면 삭제 (upsert 목적)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        
    trigger = CronTrigger(day_of_week=day_of_week, hour=hour, minute=0)
    scheduler.add_job(
        execute_reboot,
        trigger=trigger,
        args=[device_id],
        id=job_id,
        replace_existing=True
    )
    logger.info(f"Added scheduled job {job_id} for device {device_id} at {hour}:00 on days {day_of_week}")

def remove_schedule_job(schedule_id: int):
    job_id = str(schedule_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info(f"Removed scheduled job {job_id}")

def calibration_job_id(schedule_id: int) -> str:
    """재부팅 예약의 작업 id가 `str(schedule_id)`라 **접두사가 없으면 두 테이블의 id가
    겹친다** — 같은 id의 수집 예약을 등록하는 순간 재부팅 작업을 덮어쓴다."""
    return f"calibration:{schedule_id}"


def add_calibration_job(schedule_id: int, days: list[int], hour: int, minute: int):
    trigger = CronTrigger(day_of_week=to_apscheduler_dow(days), hour=hour, minute=minute)
    scheduler.add_job(execute_calibration, trigger=trigger, args=[schedule_id],
                      id=calibration_job_id(schedule_id), replace_existing=True,
                      misfire_grace_time=300, coalesce=True)
    logger.info("구조물 수집 예약 %s 등록: %02d:%02d, 요일 %s", schedule_id, hour, minute, days)


def remove_calibration_job(schedule_id: int):
    job_id = calibration_job_id(schedule_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def initialize_scheduler():
    """앱 시작 시 DB에서 활성화된 예약을 읽어 스케줄러에 일괄 등록합니다."""
    # 수집 예약 테이블은 나중에 생겼다. 테이블은 `init_db`만 만들기 때문에, 이미 운영 중인
    # DB에서 그대로 기동하면 아래 조회가 실패해 **서버가 뜨지 않는다**. 없는 것만 만든다.
    for table in (CalibrationSchedule.__table__, CalibrationRun.__table__):
        table.create(bind=engine, checkfirst=True)
    db: Session = SessionLocal()
    try:
        schedules = db.query(ScheduledReboot).filter(ScheduledReboot.is_enabled == True).all()
        for sched in schedules:
            try:
                days = json.loads(sched.days)
                add_schedule_job(sched.id, sched.device_id, days, sched.hour)
            except Exception as e:
                logger.error(f"Failed to parse schedule {sched.id}: {e}")
        for cal in db.query(CalibrationSchedule).filter(CalibrationSchedule.is_enabled == True).all():
            try:
                add_calibration_job(cal.id, json.loads(cal.days), cal.hour, cal.minute)
            except Exception as e:
                logger.error(f"Failed to register calibration schedule {cal.id}: {e}")
    finally:
        db.close()
