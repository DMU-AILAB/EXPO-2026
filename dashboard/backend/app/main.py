from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
from .config import settings
from .routers import auth, devices, cameras, rois, events, ws, stats, audio, schedules, scan
from .services.heartbeat_service import bulk_flush_heartbeats

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start APScheduler
    from .services.scheduler_service import scheduler, initialize_scheduler
    initialize_scheduler()
    scheduler.start()

    # Startup: Start background task for flushing heartbeats
    async def periodic_flush():
        while True:
            await asyncio.sleep(60)
            await bulk_flush_heartbeats()
    
    flush_task = asyncio.create_task(periodic_flush())
    yield
    # Shutdown: Stop APScheduler
    scheduler.shutdown()
    
    # Shutdown: Cancel the loop and do one final flush
    flush_task.cancel()
    try:
        await flush_task
    except asyncio.CancelledError:
        pass
    
    # Step 1 방어: 고아 데이터 증발 방어 - 마지막 강제 플러시
    await bulk_flush_heartbeats()

app = FastAPI(title="VisionGuide Backend API", lifespan=lifespan)

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
