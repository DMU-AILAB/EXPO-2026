import httpx
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..models.roi import Roi
from ..models.device import Device
from ..models.camera import Camera
from ..schemas.roi import RoiCreate, RoiUpdate, RoiResponse
from ..deps import get_current_user
from .cameras import sync_rois_to_pi, validate_camera_id, verify_etag, forward_to_pi, sync_audio_to_pi

router = APIRouter(prefix="/api/devices", tags=["ROIs"])

@router.get("/{device_id}/rois", response_model=dict)
def get_rois(
    device_id: str, 
    camera_id: str = None, 
    db: Session = Depends(get_db), 
    current_user = Depends(get_current_user)
):
    query = db.query(Roi).filter(Roi.device_id == device_id)
    if camera_id:
        query = query.filter(Roi.camera_id == camera_id)
        
    rois = query.all()
    roi_data = []
    for r in rois:
        roi_dict = {
            "id": r.id,
            "device_id": r.device_id,
            "camera_id": r.camera_id,
            "name": r.name,
            "zone_type": r.zone_type,
            "priority": r.priority,
            "announcement_text": r.announcement_text,
            "audio_file": r.audio_file,
            "polygon": json.loads(r.polygon),
            "color": r.color,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        roi_data.append(roi_dict)
        
    return {"data": roi_data, "ok": True}

@router.post("/{device_id}/rois", response_model=RoiResponse, status_code=201)
async def create_roi(
    device_id: str, 
    roi_in: RoiCreate, 
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db), 
    current_user = Depends(get_current_user)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    verify_etag(device, if_match)
        
    camera = db.query(Camera).filter(Camera.device_id == device_id, Camera.id == roi_in.camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    roi = Roi(
        device_id=device_id,
        camera_id=roi_in.camera_id,
        name=roi_in.name,
        zone_type=roi_in.zone_type,
        priority=roi_in.priority,
        announcement_text=roi_in.announcement_text,
        audio_file=roi_in.audio_file,
        color=roi_in.color,
        polygon=json.dumps(roi_in.polygon),
        is_active=roi_in.is_active
    )
    db.add(roi)
    db.flush()
    
    payload = sync_rois_to_pi(device, camera, db)
    pi_url = f"http://{device.ip}:5000/api/cameras/{roi.camera_id}/rois"
    
    try:
        if roi_in.audio_file:
            await sync_audio_to_pi(device.ip, roi_in.audio_file)
        await forward_to_pi("PUT", pi_url, payload)
    except HTTPException as e:
        db.rollback()
        # Ensure 503 is thrown for connection errors as requested
        if e.status_code in (502, 503, 504):
            raise HTTPException(status_code=503, detail="Service Unavailable: Pi is offline, cannot sync ROI.")
        raise

    device.config_etag = str(uuid.uuid4())
    db.commit()
    db.refresh(roi)
    return roi

@router.patch("/{device_id}/rois/{roi_id}", response_model=RoiResponse)
async def update_roi(
    device_id: str, 
    roi_id: int, 
    roi_in: RoiUpdate, 
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db), 
    current_user = Depends(get_current_user)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    verify_etag(device, if_match)
        
    roi = db.query(Roi).filter(Roi.device_id == device_id, Roi.id == roi_id).first()
    if not roi:
        raise HTTPException(status_code=404, detail="ROI not found")

    camera = db.query(Camera).filter(Camera.device_id == device_id, Camera.id == roi.camera_id).first()
    
    update_data = roi_in.model_dump(exclude_unset=True)
    
    # Deduplication optimization for audio_file
    new_audio_file = update_data.get('audio_file')
    old_audio_file = roi.audio_file
    should_sync_audio = new_audio_file and new_audio_file != old_audio_file

    if 'polygon' in update_data:
        update_data['polygon'] = json.dumps(update_data['polygon'])
        
    for key, value in update_data.items():
        setattr(roi, key, value)
        
    db.flush()
    
    payload = sync_rois_to_pi(device, camera, db)
    pi_url = f"http://{device.ip}:5000/api/cameras/{roi.camera_id}/rois"
    
    try:
        if should_sync_audio:
            await sync_audio_to_pi(device.ip, new_audio_file)
        await forward_to_pi("PUT", pi_url, payload)
    except HTTPException as e:
        db.rollback()
        if e.status_code in (502, 503, 504):
            raise HTTPException(status_code=503, detail="Service Unavailable: Pi is offline, cannot sync ROI.")
        raise

    device.config_etag = str(uuid.uuid4())
    db.commit()
    db.refresh(roi)
    return roi

@router.delete("/{device_id}/rois/{roi_id}", status_code=204)
async def delete_roi(
    device_id: str, 
    roi_id: int, 
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db), 
    current_user = Depends(get_current_user)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    verify_etag(device, if_match)
        
    roi = db.query(Roi).filter(Roi.device_id == device_id, Roi.id == roi_id).first()
    if not roi:
        raise HTTPException(status_code=404, detail="ROI not found")

    camera = db.query(Camera).filter(Camera.device_id == device_id, Camera.id == roi.camera_id).first()
    
    db.delete(roi)
    db.flush()
    
    payload = sync_rois_to_pi(device, camera, db)
    pi_url = f"http://{device.ip}:5000/api/cameras/{roi.camera_id}/rois"
    
    try:
        await forward_to_pi("PUT", pi_url, payload)
    except HTTPException as e:
        db.rollback()
        if e.status_code in (502, 503, 504):
            raise HTTPException(status_code=503, detail="Service Unavailable: Pi is offline, cannot sync ROI.")
        raise

    device.config_etag = str(uuid.uuid4())
    db.commit()
    return None
