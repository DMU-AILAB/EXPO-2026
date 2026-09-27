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

# ★ 기본 작업 대상은 datasets/v2 — 누수 없는 재분할본이고 `--relabel-person`이 적용된
# 현행 학습 데이터다. v1은 누수된 옛 split이라 여기서 고쳐 봐야 학습에 반영되지 않는다.
datasets_dir: Path = Path(__file__).resolve().parents[2] / "datasets" / "v2"
reviewed_path: Path = Path(__file__).parent / "reviewed.json"

# 어떤 이미지를 작업 대기열에 올릴지.
#   cane_only     — 지팡이만 있고 사람 라벨이 없는 이미지 (이 툴의 원래 용도)
#   all           — 라벨 파일이 있는 모든 이미지 (전수 검수)
#   queue         — 외부 감사 결과(JSON)가 지목한 이미지만, 지정된 순서대로
target_mode: str = "cane_only"

# 새 이미지를 열 때 자동 검출을 미리 얹을지 (기존 라벨과 겹치는 것은 빼고)
auto_suggest: bool = True
queue_path: Path | None = None

# ★ 기본적으로 train만 연다. val/test 라벨을 고치면 그때까지 측정한 모든 지표
# (리포트 §13~§15)와 비교가 깨진다 — 잣대를 바꾸면 이전 결과와 나란히 놓을 수 없다.
allowed_splits: list[str] = ["train"]

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


def _find_image(split: str, stem: str) -> str | None:
    """라벨 stem에 대응하는 이미지 파일명. 확장자를 하드코딩하지 않는다.

    현재 데이터셋은 전수 `.jpg`지만(형식 감사 확인), 나중에 다른 확장자가 섞이면
    조용히 대기열에서 빠져 **검수했다고 착각하게 된다.**
    """
    idir = datasets_dir / split / "images"
    for ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        if (idir / (stem + ext)).exists():
            return stem + ext
    return None


def _scan_targets() -> list[dict]:
    """`target_mode`에 따라 작업 대기열을 만든다."""
    if target_mode == "queue":
        return _scan_from_queue()

    targets = []
    for split in SPLITS:
        if split not in allowed_splits:
            continue
        labels_dir = datasets_dir / split / "labels"
        if not labels_dir.exists():
            continue
        for label_file in sorted(labels_dir.glob("*.txt")):
            if target_mode == "cane_only":
                classes = {b[0] for b in _parse_label_file(label_file)}
                if not (0 in classes and 1 not in classes):
                    continue
            image_file = _find_image(split, label_file.stem)
            if image_file:
                targets.append({"split": split, "filename": image_file})
    return targets


def _scan_from_queue() -> list[dict]:
    """감사 결과 JSON이 지목한 이미지만, **그 파일의 순서 그대로** 대기열에 올린다.

    `person_audit.py` 같은 도구가 "여기에 라벨이 빠졌다"를 이미 계산해 두었다면,
    그 우선순위(예: 누락 박스가 많은 순)를 사람이 그대로 따라가는 것이 가장 빠르다.
    형식: [{"img": "<파일명>", "split": "train"}, ...]  (split 생략 시 allowed_splits[0])
    """
    if queue_path is None or not queue_path.exists():
        return []
    try:
        rows = json.loads(queue_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    default_split = allowed_splits[0] if allowed_splits else "train"
    targets, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        split = row.get("split", default_split)
        name = row.get("img") or row.get("filename")
        if not name or split not in allowed_splits:
            continue
        key = f"{split}/{name}"
        if key in seen:
            continue
        stem = Path(name).stem
        image_file = _find_image(split, stem)
        if image_file and (datasets_dir / split / "labels" / f"{stem}.txt").exists():
            seen.add(key)
            targets.append({"split": split, "filename": image_file})
    return targets


def _key(split: str, filename: str) -> str:
    return f"{split}/{filename}"


def _iou(a: list[float], b: list[float]) -> float:
    """정규화 [cx, cy, w, h] 두 박스의 IoU."""
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


# 이 값을 넘게 겹치면 "이미 라벨된 것"으로 보고 제안에서 뺀다. person_audit.py와 같은 기준.
SUGGEST_DEDUP_IOU = 0.3


def _new_suggestions(split: str, filename: str, existing: list[list[float]]) -> list[list[float]]:
    """기존 사람 라벨과 겹치지 않는 자동 검출만 돌려준다.

    **겹침 제거가 핵심이다.** 일부만 라벨된 이미지(사람 5명 중 3명만 라벨)에서
    제안을 그대로 얹으면 이미 있는 3명 위에 박스가 중복으로 쌓인다. 빠진 사람만
    주황색으로 떠야 사람이 무엇을 확인해야 하는지 한눈에 보인다.
    """
    try:
        boxes = _call_local_yolo_suggest(_image_path(split, filename))
    except Exception:
        return []                       # 제안은 부가 기능 — 실패해도 작업을 막지 않는다
    return [b for b in boxes if all(_iou(b, e) < SUGGEST_DEDUP_IOU for e in existing)]


def _build_item(split: str, filename: str) -> dict:
    label_path = _label_path(split, filename)
    boxes = _parse_label_file(label_path)
    cane_boxes = [list(b[1:]) for b in boxes if b[0] == 0]
    person_boxes = [list(b[1:]) for b in boxes if b[0] == 1]
    reviewed = _reviewed.get(_key(split, filename), False)
    # 아직 검수 전인 이미지만 제안을 미리 얹는다 — 이미 사람이 확인한 이미지를 다시
    # 열었을 때(이전 버튼) 지웠던 제안이 되살아나면 작업을 되돌리는 셈이 된다.
    auto = [] if (reviewed or not auto_suggest) else _new_suggestions(split, filename, person_boxes)
    return {
        "split": split,
        "filename": filename,
        "cane_boxes": cane_boxes,
        "person_boxes": person_boxes,
        "auto_boxes": auto,
        "reviewed": reviewed,
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
_LOCAL_YOLO_PT_PATH = Path(__file__).resolve().parents[2] / "weights" / "yolov8n.pt"
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
    person_boxes: list[list[float]]              # [[cx, cy, w, h], ...] 정규화 0~1
    # 지팡이 박스. **None이면 기존 class 0 라인을 그대로 보존**한다 — 지팡이를 편집하지
    # 않는 클라이언트가 실수로 지워 버리는 일이 없도록 "미전달"과 "빈 목록"을 구분한다.
    cane_boxes: list[list[float]] | None = None


@app.post("/api/save")
async def save_item(payload: SavePayload):
    _ensure_loaded()
    # 잣대 보호 — val/test를 열지 않는 것이 기본이지만, 저장 경로에서도 한 번 더 막는다.
    if payload.split not in allowed_splits:
        raise HTTPException(status_code=403,
                            detail=f"'{payload.split}' split은 편집이 허용되지 않았다 "
                                   f"(허용: {allowed_splits}). val/test를 고치면 "
                                   f"이전 평가 결과와 비교가 깨진다.")
    label_path = _label_path(payload.split, payload.filename)
    if not label_path.exists():
        raise HTTPException(status_code=404, detail="label file not found")

    existing = _parse_label_file(label_path)
    if payload.cane_boxes is None:
        cane_lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
                      for c, cx, cy, w, h in existing if c != 1]
    else:
        cane_lines = [f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for cx, cy, w, h in payload.cane_boxes]
    person_lines = [f"1 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for cx, cy, w, h in payload.person_boxes]
    lines = cane_lines + person_lines

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

    parser = argparse.ArgumentParser(
        description="지팡이 데이터셋 라벨 보완/검수 툴",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  # 감사가 지목한 이미지만 (권장) — 누락이 많은 순서대로
  python apps/label_tool/server.py --targets queue --queue missing.json

  # 전수 검수
  python apps/label_tool/server.py --targets all

  # 원래 용도 (지팡이만 있고 사람 라벨이 없는 이미지)
  python apps/label_tool/server.py --targets cane_only
""")
    parser.add_argument("--datasets-dir",
                        default=str(Path(__file__).resolve().parents[2] / "datasets" / "v2"),
                        help="작업할 데이터셋 루트 (기본: datasets/v2 — 현행 학습 데이터)")
    parser.add_argument("--targets", choices=("cane_only", "all", "queue"), default="cane_only",
                        help="대기열 구성 방식 (기본: cane_only)")
    parser.add_argument("--queue", default=None,
                        help="--targets queue에서 쓸 JSON: [{\"img\":..., \"split\":...}, ...]")
    parser.add_argument("--splits", default="train",
                        help="편집을 허용할 split, 쉼표 구분 (기본: train). "
                             "**val/test를 열면 평가 잣대가 바뀌어 이전 결과와 비교가 깨진다**")
    parser.add_argument("--no-auto-suggest", action="store_true",
                        help="새 이미지에서 자동 검출을 미리 얹지 않는다")
    parser.add_argument("--reviewed", default=None,
                        help="검토 이력 파일 (기본: apps/label_tool/reviewed.json). "
                             "대기열을 바꿔 작업할 때는 따로 두는 편이 헷갈리지 않는다")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()

    target_mode = args.targets
    auto_suggest = not args.no_auto_suggest
    allowed_splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    if args.queue:
        queue_path = Path(args.queue).expanduser().resolve()
    if args.reviewed:
        reviewed_path = Path(args.reviewed).expanduser().resolve()
    if target_mode == "queue" and queue_path is None:
        parser.error("--targets queue 를 쓰려면 --queue <json> 이 필요하다")

    bad = [s for s in allowed_splits if s not in SPLITS]
    if bad:
        parser.error(f"알 수 없는 split: {bad} (가능: {SPLITS})")
    if set(allowed_splits) - {"train"}:
        print(f"[WARN] train 이외의 split을 편집 대상으로 열었다: {allowed_splits}\n"
              f"       val/test 라벨을 고치면 리포트 §13~§15의 지표와 비교가 깨진다.")

    datasets_dir = Path(args.datasets_dir).resolve()
    _ensure_loaded()
    print(f"[Label Tool] datasets_dir : {datasets_dir}")
    print(f"[Label Tool] 대기열 방식  : {target_mode}"
          + (f"  ({queue_path})" if target_mode == "queue" else ""))
    print(f"[Label Tool] 편집 허용    : {allowed_splits}")
    print(f"[Label Tool] 자동 제안    : {'켜짐 (기존 라벨과 겹치는 것은 제외)' if auto_suggest else '꺼짐'}")
    print(f"[Label Tool] 대상 이미지  : {len(_targets)}장")
    print(f"[Label Tool] reviewed     : {reviewed_path}")
    print(f"[Label Tool] → http://localhost:{args.port}")

    uvicorn.run(app, host="0.0.0.0", port=args.port)
