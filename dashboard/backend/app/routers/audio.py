import json
import os
import shutil
import subprocess
import uuid
import wave
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
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
MAX_TTS_TEXT_CHARS = 300


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TTS_TEXT_CHARS)
    label: Optional[str] = Field(None, max_length=120)
    voice: Optional[str] = Field(None, max_length=200)
    rate: int = Field(0, ge=-10, le=10)


def _powershell_path() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell.exe")


def _wav_duration(path: str) -> float:
    with wave.open(path, "rb") as audio:
        frame_rate = audio.getframerate()
        return audio.getnframes() / frame_rate if frame_rate else 0.0


def _audio_duration(path: str, suffix: str) -> float:
    if suffix == ".wav":
        return _wav_duration(path)
    return float(MP3(path).info.length)


def _generate_windows_tts(path: str, request: TtsRequest, powershell: str) -> None:
    script = r'''
$ErrorActionPreference = "Stop"
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($payload.voice) {
        try { $synth.SelectVoice([string]$payload.voice) } catch { }
    }
    $synth.Rate = [int]$payload.rate
    $synth.SetOutputToWaveFile([string]$payload.path)
    $synth.Speak([string]$payload.text)
} finally {
    $synth.Dispose()
}
'''
    payload = {
        "path": path,
        "text": request.text,
        "voice": request.voice or "",
        "rate": request.rate,
    }
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not os.path.exists(path):
        detail = (result.stderr or result.stdout or "Windows 로컬 TTS가 음성을 생성하지 못했습니다.").strip()
        raise RuntimeError(detail[-500:])


def _audio_response(audio: Audio) -> dict:
    return {
        "filename": audio.filename,
        "label": audio.label,
        "size_bytes": audio.size_bytes,
    }

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
    original_name = file.filename or ""
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".mp3", ".wav"}:
        raise HTTPException(status_code=400, detail="Only .mp3 or .wav files are allowed")

    # Secure filename via basename
    safe_filename = os.path.basename(original_name)
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
        duration = _audio_duration(file_path, suffix)
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"Invalid audio file or cannot read metadata: {str(e)}")
    if duration > MAX_DURATION_SECONDS:
        os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"Audio duration exceeds 10 seconds (length: {duration:.1f}s)")

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
        "data": _audio_response(existing_audio),
        "ok": True
    }


@router.post("/tts", response_model=AudioUploadResponse, status_code=201)
def generate_tts(
    request: TtsRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Generate a local Windows SAPI voice file for an ROI announcement."""
    powershell = _powershell_path()
    if not powershell:
        raise HTTPException(
            status_code=503,
            detail="로컬 TTS는 Windows PC에서만 사용할 수 있습니다.",
        )

    filename = f"tts_{uuid.uuid4().hex[:12]}.wav"
    file_path = str(Path(settings.audio_dir) / filename)
    try:
        _generate_windows_tts(file_path, request, powershell)
        duration = _audio_duration(file_path, ".wav")
        if duration > MAX_DURATION_SECONDS:
            raise ValueError(f"Audio duration exceeds 10 seconds (length: {duration:.1f}s)")
        size_bytes = os.path.getsize(file_path)
        if size_bytes > MAX_AUDIO_SIZE_BYTES:
            raise ValueError("Audio file size exceeds 10MB")
    except Exception as exc:
        try:
            os.remove(file_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=f"TTS 음성 생성에 실패했습니다: {exc}") from exc

    audio = Audio(
        filename=filename,
        label=request.label.strip() if request.label and request.label.strip() else f"TTS: {request.text[:40]}",
        size_bytes=size_bytes,
    )
    db.add(audio)
    db.commit()
    db.refresh(audio)
    return {"data": _audio_response(audio), "ok": True}

@router.get("/{filename}")
def stream_audio(filename: str, current_user = Depends(get_current_user)):
    """ROI 편집 화면의 미리듣기.

    인증이 빠져 있었다 — 업로드는 관리자만 할 수 있는데 재생은 누구나 가능했다.""" 
    # Secure the requested filename
    safe_filename = os.path.basename(filename)
    
    file_path = os.path.join(settings.audio_dir, safe_filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404,
                            detail={"error": "AUDIO_NOT_FOUND", "message": "오디오 파일이 없습니다"})
        
    media_type = "audio/wav" if Path(safe_filename).suffix.lower() == ".wav" else "audio/mpeg"
    return FileResponse(file_path, media_type=media_type)
