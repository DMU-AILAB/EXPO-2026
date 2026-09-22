import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import Optional
from mutagen.mp3 import MP3

from ..database import get_db
from ..deps import get_current_user
from ..models.audio import Audio
from ..schemas.audio import AudioListResponse, AudioUploadResponse
from ..config import settings

router = APIRouter(prefix="/api/audio", tags=["Audio"])

# Ensure audio directory exists
os.makedirs(settings.audio_dir, exist_ok=True)

MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_DURATION_SECONDS = 10.0

@router.get("", response_model=AudioListResponse)
def list_audio_files(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    audios = db.query(Audio).all()
    data = []
    for a in audios:
        data.append({
            "filename": a.filename,
            "label": a.label,
            "size_bytes": a.size_bytes
        })
    return {"data": data, "ok": True}

@router.post("/upload", response_model=AudioUploadResponse, status_code=201)
async def upload_audio_file(
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    if not file.filename.lower().endswith('.mp3'):
        raise HTTPException(status_code=400, detail="Only .mp3 files are allowed")

    # Secure filename via basename
    safe_filename = os.path.basename(file.filename)
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = os.path.join(settings.audio_dir, safe_filename)
    
    # Save the file temporarily
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    file_size = os.path.getsize(file_path)
    
    if file_size > MAX_AUDIO_SIZE_BYTES:
        os.remove(file_path)
        raise HTTPException(status_code=400, detail="File size exceeds 10MB limit")
        
    # Check duration using mutagen
    try:
        audio = MP3(file_path)
        if audio.info.length > MAX_DURATION_SECONDS:
            os.remove(file_path)
            raise HTTPException(status_code=400, detail=f"Audio duration exceeds 10 seconds (length: {audio.info.length:.1f}s)")
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"Invalid MP3 file or cannot read metadata: {str(e)}")

    # Update or Create DB entry
    existing_audio = db.query(Audio).filter(Audio.filename == safe_filename).first()
    if existing_audio:
        existing_audio.size_bytes = file_size
        existing_audio.label = label if label is not None else existing_audio.label
    else:
        new_audio = Audio(
            filename=safe_filename,
            label=label,
            size_bytes=file_size
        )
        db.add(new_audio)
        existing_audio = new_audio
        
    db.commit()
    db.refresh(existing_audio)
    
    return {
        "data": {
            "filename": existing_audio.filename,
            "label": existing_audio.label,
            "size_bytes": existing_audio.size_bytes
        },
        "ok": True
    }

@router.get("/{filename}")
def stream_audio(filename: str):
    # Secure the requested filename
    safe_filename = os.path.basename(filename)
    
    file_path = os.path.join(settings.audio_dir, safe_filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="AUDIO_NOT_FOUND")
        
    return FileResponse(file_path, media_type="audio/mpeg")
