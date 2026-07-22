#!/usr/bin/env python3
"""label_tool/server.py — 지팡이 데이터셋의 "사람 라벨 누락" 보완 전용 라벨링 툴.

datasets/{train,val,test}/labels 중 class 0(white_cane)만 있고 class 1(person)이
없는 이미지("cane_only")만 골라 순서대로 보여주고, 사람 바운딩 박스를 그려 저장한다.
기존 지팡이 라벨은 읽기 전용 참고용으로만 표시하고 건드리지 않는다.

PC에서 로컬로 실행 (데이터셋 준비 작업 — Pi와 무관):
    python label_tool/server.py --datasets-dir datasets --port 5050
    브라우저: http://localhost:5050

자동 제안 기능(둘 다 사람이 검수 후 저장하는 구조 — /api/suggest는 라벨 파일을 절대
직접 쓰지 않는다):
    - local_yolo(기본값): label_tool/yolov8n_human.pt(COCO 사전학습 YOLOv8n, class 0=person)로
      로컬 추론. 네트워크/API 키/과금 없음, 빠름. 이 프로젝트가 파인튜닝한 white_cane_v2/
      v3_320은 지금 고치려는 "사람 라벨 누락" 문제로 오염돼 있어 제안용으로 쓰지 않는다.
    - openai: pip install openai 후 환경변수 OPENAI_API_KEY 설정. GPT vision 호출이라
      네트워크/비용 발생, 바운딩박스 정밀도도 전용 탐지 모델보다 낮은 편이라 보조 옵션.
    UI의 provider 선택 드롭다운으로 전환 가능.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

OPENAI_MODEL = os.environ.get("OPENAI_LABEL_MODEL", "gpt-4o-mini")

_PERSON_BOX_SCHEMA = {
    "name": "person_boxes",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "바운딩박스 중심 x, 이미지 너비 대비 0~1 정규화"},
                        "cy": {"type": "number", "description": "바운딩박스 중심 y, 이미지 높이 대비 0~1 정규화"},
                        "w":  {"type": "number", "description": "바운딩박스 너비, 이미지 너비 대비 0~1 정규화"},
                        "h":  {"type": "number", "description": "바운딩박스 높이, 이미지 높이 대비 0~1 정규화"},
                    },
                    "required": ["cx", "cy", "w", "h"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["people"],
        "additionalProperties": False,
    },
}

_SUGGEST_PROMPT = (
    "이 이미지에 보이는 모든 사람(사람 몸 전체 또는 일부)에 대해 바운딩박스를 찾아줘. "
    "각 박스는 중심좌표(cx,cy)와 너비/높이(w,h)를 이미지 크기 대비 0~1 사이 정규화된 "
    "값으로 표현해. 사람이 한 명도 없으면 빈 배열을 반환해."
)

STATIC_DIR = Path(__file__).parent / "static"
SPLITS = ["train", "val", "test"]

datasets_dir: Path = Path(__file__).parent.parent / "datasets"
reviewed_path: Path = Path(__file__).parent / "reviewed.json"

app = FastAPI(title="VisionGuide Cane-Dataset Person Labeling Tool",
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# {split}/{filename} 키 목록 — 최초 요청 시 1회만 스캔해 메모리에 캐싱 (9천여 개 라벨
# 파일을 매 요청마다 다시 읽지 않기 위해서). reviewed 상태만 별도 파일로 분리해 즉시
# 반영/재시작 안전. None = 아직 로드 안 됨 (테스트에서 datasets_dir를 바꾼 뒤
# _reset_cache()로 강제 재로드할 수 있도록 시작 이벤트 대신 지연 로딩 방식을 쓴다).
_targets: list[dict] | None = None
_reviewed: dict[str, bool] | None = None


def _reset_cache() -> None:
    global _targets, _reviewed
    _targets = None
    _reviewed = None


def _ensure_loaded() -> None:
    global _targets, _reviewed
    if _targets is None:
        _targets = _scan_targets()
    if _reviewed is None:
        _reviewed = _load_reviewed()


def _label_path(split: str, filename: str) -> Path:
    return datasets_dir / split / "labels" / (Path(filename).stem + ".txt")


def _image_path(split: str, filename: str) -> Path:
    return datasets_dir / split / "images" / filename


def _parse_label_file(path: Path) -> list[tuple[int, float, float, float, float]]:
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        cls, cx, cy, w, h = parts
        boxes.append((int(cls), float(cx), float(cy), float(w), float(h)))
    return boxes


def _load_reviewed() -> dict[str, bool]:
    try:
        return json.loads(reviewed_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_reviewed() -> None:
    """atomic write — roi_editor/server.py의 _save()와 동일 패턴."""
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=reviewed_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_reviewed, f, ensure_ascii=False, indent=2)
        os.replace(tmp, reviewed_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _scan_targets() -> list[dict]:
    """cane_only(class 0만 있고 class 1은 없는) 이미지 목록을 전체 split에서 스캔."""
    targets = []
    for split in SPLITS:
        labels_dir = datasets_dir / split / "labels"
        if not labels_dir.exists():
            continue
        for label_file in sorted(labels_dir.glob("*.txt")):
            boxes = _parse_label_file(label_file)
            classes = {b[0] for b in boxes}
            if 0 in classes and 1 not in classes:
                image_file = label_file.stem + ".jpg"
                if (datasets_dir / split / "images" / image_file).exists():
                    targets.append({"split": split, "filename": image_file})
    return targets


def _key(split: str, filename: str) -> str:
    return f"{split}/{filename}"


def _build_item(split: str, filename: str) -> dict:
    label_path = _label_path(split, filename)
    boxes = _parse_label_file(label_path)
    cane_boxes = [list(b[1:]) for b in boxes if b[0] == 0]
    person_boxes = [list(b[1:]) for b in boxes if b[0] == 1]
    return {
        "split": split,
        "filename": filename,
        "cane_boxes": cane_boxes,
        "person_boxes": person_boxes,
        "reviewed": _reviewed.get(_key(split, filename), False),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stats")
async def get_stats():
    _ensure_loaded()
    total = len(_targets)
    reviewed = sum(1 for t in _targets if _reviewed.get(_key(t["split"], t["filename"])))
    return {"total": total, "reviewed": reviewed, "remaining": total - reviewed}


@app.get("/api/next")
async def get_next(after_index: int = -1):
    """after_index 다음부터 미검토 항목을 찾아 반환 — 프런트가 순번(index)도 함께 받아
    '이전' 내비게이션에 쓴다. 다 끝나면 done=true."""
    _ensure_loaded()
    for i in range(after_index + 1, len(_targets)):
        t = _targets[i]
        if not _reviewed.get(_key(t["split"], t["filename"])):
            return {"done": False, "index": i, **_build_item(t["split"], t["filename"])}
    return {"done": True, "index": len(_targets)}


@app.get("/api/item/{index}")
async def get_item(index: int):
    _ensure_loaded()
    if not (0 <= index < len(_targets)):
        raise HTTPException(status_code=404, detail="index out of range")
    t = _targets[index]
    return {"done": False, "index": index, **_build_item(t["split"], t["filename"])}


@app.get("/api/image/{split}/{filename}")
async def get_image(split: str, filename: str):
    path = _image_path(split, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path)


def _call_openai_suggest(image_path: Path) -> list[list[float]]:
    """OpenAI vision 모델에게 사람 바운딩박스를 물어본다. 결과는 참고용 제안일 뿐이며
    이 함수는 어떤 라벨 파일도 쓰지 않는다 — 저장은 항상 사람이 /api/save를 눌러야 일어난다."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai 패키지가 설치되지 않았습니다 — pip install openai 로 설치하세요")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다")

    b64 = base64.b64encode(image_path.read_bytes()).decode()
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _SUGGEST_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
            ],
        }],
        response_format={"type": "json_schema", "json_schema": _PERSON_BOX_SCHEMA},
    )
    data = json.loads(response.choices[0].message.content)
    return [[p["cx"], p["cy"], p["w"], p["h"]] for p in data["people"]]


# COCO 사전학습 YOLOv8n(class 0 = person) — 이 프로젝트가 파인튜닝한 white_cane_v2/v3_320은
# 지금 고치려는 바로 그 "사람 라벨 누락" 문제로 오염돼 있어 제안용으로 쓰기엔 부적합하다.
# COCO 원본은 별도로 정상 라벨링된 사람 클래스라 이 용도에 훨씬 적합하고, 로컬 추론이라
# 네트워크/과금도 없다.
#
# GPU 없는 PC라 CPU 추론 속도를 위해 OpenVINO INT8로 양자화된 모델을 우선 사용한다
# (Intel CPU 전용 최적화 런타임 — PyTorch eager 대비 CPU에서 보통 2~4배 빠름). 양자화
# 보정(calibration)은 이 프로젝트 자체 val 이미지 300장으로 했다(범용 COCO 샘플보다
# 실제 추론 대상과 분포가 비슷해 정확도 손실이 더 적다). 내보내기 명령:
#   yolo export model=yolov8n.pt format=openvino int8=True \
#       data=datasets/data_local.yaml imgsz=640
# (내보내기 결과는 <model_stem>_int8_openvino_model/ 폴더로 나오므로, yolov8n.pt 기준
# 결과 폴더명을 yolov8n_human_int8_openvino_model로 옮겨 위 경로와 맞춰준다)
# OpenVINO 결과물이 없으면(예: openvino 패키지 미설치 환경) 원본 fp32 .pt로 자동 폴백 —
# 저장소 루트의 yolov8n.pt(COCO 사전학습, 이미 git에 있음)를 그대로 재사용한다. label_tool
# 안에 따로 사본을 두면 완전히 같은 6.5MB 파일이 중복 커밋되므로 만들지 않는다.
_LOCAL_YOLO_OPENVINO_PATH = Path(__file__).parent / "yolov8n_human_int8_openvino_model"
_LOCAL_YOLO_PT_PATH = Path(__file__).parent.parent / "yolov8n.pt"
_LOCAL_YOLO_CONF = 0.35
_local_yolo_model = None


def _call_local_yolo_suggest(image_path: Path) -> list[list[float]]:
    global _local_yolo_model
    if _local_yolo_model is None:
        from ultralytics import YOLO
        if _LOCAL_YOLO_OPENVINO_PATH.exists():
            _local_yolo_model = YOLO(str(_LOCAL_YOLO_OPENVINO_PATH))
        elif _LOCAL_YOLO_PT_PATH.exists():
            _local_yolo_model = YOLO(str(_LOCAL_YOLO_PT_PATH))
        else:
            raise RuntimeError(
                f"{_LOCAL_YOLO_OPENVINO_PATH} / {_LOCAL_YOLO_PT_PATH} 둘 다 없음 — "
                "COCO 사전학습 yolov8n 가중치를 준비하세요")

    results = _local_yolo_model.predict(str(image_path), conf=_LOCAL_YOLO_CONF, classes=[0], verbose=False)
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return []
    return [[round(v, 6) for v in xywhn] for xywhn in boxes.xywhn.tolist()]


_SUGGEST_PROVIDER_NAMES = {"local_yolo", "openai"}


@app.post("/api/suggest/{index}")
async def suggest_person_boxes(index: int, provider: str = "local_yolo"):
    _ensure_loaded()
    if not (0 <= index < len(_targets)):
        raise HTTPException(status_code=404, detail="index out of range")
    if provider not in _SUGGEST_PROVIDER_NAMES:
        raise HTTPException(status_code=400, detail=f"알 수 없는 provider: {provider}")
    t = _targets[index]
    try:
        # 모듈 전역 이름으로 직접 호출 — dict에 함수 객체를 미리 바인딩해두면 테스트에서
        # monkeypatch.setattr(srv, "_call_openai_suggest", ...)로 갈아끼워도 반영이 안 된다.
        if provider == "openai":
            boxes = _call_openai_suggest(_image_path(t["split"], t["filename"]))
        else:
            boxes = _call_local_yolo_suggest(_image_path(t["split"], t["filename"]))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{provider} 호출 실패: {exc}")
    return {"boxes": boxes, "provider": provider}


class SavePayload(BaseModel):
    split: str
    filename: str
    person_boxes: list[list[float]]  # [[cx, cy, w, h], ...] 정규화 0~1


@app.post("/api/save")
async def save_item(payload: SavePayload):
    _ensure_loaded()
    label_path = _label_path(payload.split, payload.filename)
    if not label_path.exists():
        raise HTTPException(status_code=404, detail="label file not found")

    existing = _parse_label_file(label_path)
    kept_lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
                  for c, cx, cy, w, h in existing if c != 1]
    new_lines = [f"1 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for cx, cy, w, h in payload.person_boxes]
    lines = kept_lines + new_lines

    fd, tmp = tempfile.mkstemp(dir=label_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, label_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    _reviewed[_key(payload.split, payload.filename)] = True
    _save_reviewed()
    return {"ok": True, "person_count": len(payload.person_boxes)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-dir", default=str(Path(__file__).parent.parent / "datasets"))
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()

    datasets_dir = Path(args.datasets_dir).resolve()
    print(f"[Label Tool] datasets_dir: {datasets_dir}")
    print(f"[Label Tool] reviewed_path: {reviewed_path}")

    uvicorn.run(app, host="0.0.0.0", port=args.port)
