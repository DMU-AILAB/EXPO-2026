import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Header
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
import re
import json
import uuid

import jwt
from ..database import get_db
from ..config import settings
from ..models import User
from ..models.camera import Camera
from ..models.device import Device
from ..models.roi import Roi
from ..schemas.camera import CameraResponse, CameraUpdate, DetectionParamsUpdate, DetectionParamsResponse
from ..deps import get_current_user
from fastapi import Query
from fastapi.responses import StreamingResponse
import asyncio

ACTIVE_STREAMS = 0

router = APIRouter(prefix="/api/devices", tags=["Cameras"])

CAMERA_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

def validate_camera_id(camera_id: str = Path(...)):
    if not CAMERA_ID_REGEX.match(camera_id):
        raise HTTPException(status_code=400, detail="Invalid camera_id format")
    return camera_id

def verify_etag(device: Device, if_match: Optional[str] = Header(None)):
    if if_match is None:
        raise HTTPException(status_code=400, detail="If-Match header is required for updates")
    clean_etag = if_match.strip('"')
    if device.config_etag and device.config_etag != clean_etag:
        raise HTTPException(status_code=412, detail="Precondition Failed: config_etag mismatch")

async def forward_to_pi(method: str, url: str, json_data: dict = None):
    try:
        async with httpx.AsyncClient() as client:
            res = await client.request(method, url, json=json_data, timeout=5.0)
            if res.status_code >= 400:
                raise HTTPException(status_code=res.status_code, detail=f"Pi error: {res.text}")
            return res
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Gateway Timeout: Pi is not responding ({str(e)})")
    except httpx.ConnectError as e:
        raise HTTPException(status_code=503, detail=f"Service Unavailable: Pi is offline ({str(e)})")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Bad Gateway: Failed to communicate with Pi ({str(e)})")

async def sync_audio_to_pi(device_ip: str, filename: str):
    import os
    file_path = settings.audio_dir / filename
    if not os.path.exists(file_path):
        return  # File not found on server, maybe it was deleted

    pi_url = f"http://{device_ip}:5000/api/audio/upload"
    try:
        async with httpx.AsyncClient() as client:
            with open(file_path, "rb") as f:
                files = {"file": (filename, f, "audio/mpeg")}
                res = await client.post(pi_url, files=files, timeout=5.0)
                if res.status_code >= 400:
                    raise HTTPException(status_code=res.status_code, detail=f"Pi error (Audio): {res.text}")
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Gateway Timeout: Pi is not responding ({str(e)})")
    except httpx.ConnectError as e:
        raise HTTPException(status_code=503, detail=f"Service Unavailable: Pi is offline ({str(e)})")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Bad Gateway: Failed to communicate with Pi ({str(e)})")

@router.get("/{device_id}/cameras", response_model=dict)
def get_cameras(device_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    cameras = db.query(Camera).filter(Camera.device_id == device_id).all()
    
    camera_data = []
    for cam in cameras:
        roi_count = db.query(Roi).filter(Roi.camera_id == cam.id, Roi.device_id == device_id).count()
        camera_data.append({
            "id": cam.id,
            "port": cam.port,
            "capture_preset": cam.capture_preset,
            "fps": cam.fps,
            "model_variant": cam.model_variant,
            "rotation": cam.rotation,
            "require_person": cam.require_person,
            "is_active": cam.is_active,
            "roi_count": roi_count,
            "today_detections": 0,
            "is_streaming": True,
            "current_alert": None
        })
        
    return {"data": camera_data, "ok": True}

@router.patch("/{device_id}/cameras/{camera_id}", response_model=CameraResponse)
async def update_camera(
    device_id: str, 
    camera_update: CameraUpdate,
    camera_id: str = Depends(validate_camera_id),
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    verify_etag(device, if_match)
        
    camera = db.query(Camera).filter(Camera.device_id == device_id, Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    update_data = camera_update.model_dump(exclude_unset=True)
    
    from ..schemas.camera import CapturePreset, ModelVariant
    for k, v in update_data.items():
        if isinstance(v, (CapturePreset, ModelVariant)):
            update_data[k] = v.value

    if update_data:
        pi_url = f"http://{device.ip}:5000/api/cameras/{camera_id}"
        await forward_to_pi("PATCH", pi_url, update_data)

        for key, value in update_data.items():
            setattr(camera, key, value)
        
        device.config_etag = str(uuid.uuid4())
        db.commit()
        db.refresh(camera)
        
    return camera

@router.get("/{device_id}/cameras/{camera_id}/detection-params", response_model=dict)
def get_detection_params(
    device_id: str, 
    camera_id: str = Depends(validate_camera_id),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    camera = db.query(Camera).filter(Camera.device_id == device_id, Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
        
    return {
        "data": {
            "conf": {
                "white_cane": camera.conf_white_cane,
                "person": camera.conf_person
            },
            "cooldown": camera.cooldown,
            "debounce": camera.debounce
        },
        "ok": True
    }

def sync_rois_to_pi(device: Device, camera: Camera, db: Session):
    rois = db.query(Roi).filter(Roi.device_id == device.id, Roi.camera_id == camera.id, Roi.is_active == True).all()
    
    roi_list = []
    for r in rois:
        audio_path = r.audio_file
        if audio_path and not audio_path.startswith('/'):
            audio_path = f"/home/ailab/visionguide/audio/{audio_path}"
            
        roi_item = {
            "name": r.name,
            "zone_type": r.zone_type,
            "priority": r.priority,
            "announcement_text": r.announcement_text,
            "audio_file": audio_path,
            "points": json.loads(r.polygon)
        }
        # Explicitly not sending 'color' as Pi doesn't understand it
        roi_list.append(roi_item)
        
    payload = {
        "rois": roi_list,
        "conf": {
            "white_cane": camera.conf_white_cane,
            "person": camera.conf_person
        },
        "cooldown": camera.cooldown,
        "debounce": camera.debounce
    }

    return payload

@router.patch("/{device_id}/cameras/{camera_id}/detection-params", response_model=DetectionParamsUpdate)
async def update_detection_params(
    device_id: str, 
    params: DetectionParamsUpdate,
    camera_id: str = Depends(validate_camera_id),
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    verify_etag(device, if_match)
        
    camera = db.query(Camera).filter(Camera.device_id == device_id, Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    update_data = params.model_dump(exclude_unset=True)
    
    if 'conf' in update_data and isinstance(update_data['conf'], dict):
        conf_dict = update_data['conf']
        if 'white_cane' in conf_dict:
            camera.conf_white_cane = conf_dict['white_cane']
        if 'person' in conf_dict:
            camera.conf_person = conf_dict['person']
            
    if 'cooldown' in update_data:
        camera.cooldown = update_data['cooldown']
    if 'debounce' in update_data:
        camera.debounce = update_data['debounce']
        
    payload = sync_rois_to_pi(device, camera, db)
    pi_url = f"http://{device.ip}:5000/api/cameras/{camera_id}/rois"
    
    await forward_to_pi("PUT", pi_url, payload)

    device.config_etag = str(uuid.uuid4())
    db.commit()
    
    return params

async def get_current_user_or_query(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    jwt_token = None
    if authorization and authorization.startswith("Bearer "):
        jwt_token = authorization.split(" ")[1]
    elif token:
        jwt_token = token
        
    if not jwt_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    try:
        payload = jwt.decode(jwt_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
            
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
            
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

@router.get("/{device_id}/cameras/{camera_id}/stream")
async def proxy_mjpeg_stream(
    device_id: str,
    camera_id: str = Depends(validate_camera_id),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_or_query)
):
    global ACTIVE_STREAMS
    
    if ACTIVE_STREAMS >= 5:
        raise HTTPException(status_code=503, detail="STREAM_CAPACITY_FULL")
        
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="DEVICE_NOT_FOUND")
        
    camera = db.query(Camera).filter(Camera.device_id == device_id, Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
        
    target_url = f"http://{device.ip}:{camera.port}/stream.mjpg"
    
    async def stream_generator():
        global ACTIVE_STREAMS
        ACTIVE_STREAMS += 1
        client = httpx.AsyncClient()
        try:
            async with client.stream("GET", target_url, timeout=None) as response:
                if response.status_code != 200:
                    yield b""
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
        except (httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError, Exception, asyncio.CancelledError):
            pass
        finally:
            await client.aclose()
            ACTIVE_STREAMS -= 1
            
    return StreamingResponse(
        stream_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
