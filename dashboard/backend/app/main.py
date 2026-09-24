from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging

from .config import settings
from .errors import register_error_handlers
from .routers import auth, devices, cameras, rois, events, ws, stats, audio, schedules, scan
from .services.heartbeat_service import bulk_flush_heartbeats
from .services.monitor_service import broadcast_camera_alerts, sweep_offline_devices
from .services.foot_traffic_puller import PULL_INTERVAL_SEC, pull_once

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start APScheduler
    from .services.scheduler_service import scheduler, initialize_scheduler
    initialize_scheduler()
    scheduler.start()

    # 백그라운드 루프 세 개. 각자 주기가 다르고 실패해도 서로를 멈추지 않아야 한다 —
    # 한 루프의 예외가 태스크를 끝내면 그 기능만 조용히 죽는다.
    async def _loop(name: str, interval: float, fn):
        while True:
            await asyncio.sleep(interval)
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:                      # noqa: BLE001
                logger.exception("%s 루프에서 오류 — 다음 주기에 계속합니다", name)

    async def _flush_and_sweep():
        # 순서에 의미가 있다: 버퍼를 DB에 내린 뒤에 생사를 판정해야 방금 도착한
        # 하트비트가 반영된 last_seen을 본다.
        await bulk_flush_heartbeats()
        await sweep_offline_devices()
        await broadcast_camera_alerts()

    tasks = [
        asyncio.create_task(_loop("heartbeat-flush", 30, _flush_and_sweep)),
        asyncio.create_task(_loop("foot-traffic", PULL_INTERVAL_SEC, pull_once)),
    ]
    yield
    # Shutdown: Stop APScheduler
    scheduler.shutdown()

    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass

    # 고아 데이터 증발 방어 — 마지막 강제 플러시
    await bulk_flush_heartbeats()

app = FastAPI(title="VisionGuide Backend API", lifespan=lifespan)

# 명세 §1.4·§16의 오류 형식을 여기 한 곳에서 강제한다.
register_error_handlers(app)

origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(cameras.router)
app.include_router(rois.router)
app.include_router(events.router)
app.include_router(ws.router)
app.include_router(stats.router)
app.include_router(audio.router)
app.include_router(schedules.router)
app.include_router(scan.router)

@app.get("/")
def read_root():
    return {"status": "VisionGuide Backend is running", "ok": True}
