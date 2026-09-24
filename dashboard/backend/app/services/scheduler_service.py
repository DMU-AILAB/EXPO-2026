import logging
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
import json

from ..database import SessionLocal
from ..models.schedule import ScheduledReboot
from ..models.device import Device

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

        ip = device.ip
        logger.info(f"Executing scheduled reboot for {device_id} at {ip}")
        
        # 짧은 타임아웃 적용 (Offline 상태 기기로 인한 스레드 점유 방지)
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"http://{ip}:5000/reboot")
            
    except (httpx.RequestError, httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
        # Step 3 방어: 스케줄 미스(기기 오프라인 등) 시 예외를 삼키고 다음 주기 대기
        logger.warning(f"Scheduled reboot failed for {device_id} ({e}). Skipping to next cycle.")
    except Exception as e:
        logger.error(f"Unexpected error during scheduled reboot for {device_id}: {e}")
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

def initialize_scheduler():
    """앱 시작 시 DB에서 활성화된 예약을 읽어 스케줄러에 일괄 등록합니다."""
    db: Session = SessionLocal()
    try:
        schedules = db.query(ScheduledReboot).filter(ScheduledReboot.is_enabled == True).all()
        for sched in schedules:
            try:
                days = json.loads(sched.days)
                add_schedule_job(sched.id, sched.device_id, days, sched.hour)
            except Exception as e:
                logger.error(f"Failed to parse schedule {sched.id}: {e}")
    finally:
        db.close()
