"""camera_config.py — 다중 카메라 프로필 설정 (camera_config.json).

camera_live_pi.py가 카메라 1대(레거시 CLI 모드) 또는 여러 대(camera_config.json 지정 시)를
독립 파이프라인으로 동시 구동할 때 쓰는 카메라별 설정. rois.json과 동일한 원칙으로
Pi 로컬에서 roi_editor가 생성/수정하며, PC에서 rsync로 배포하지 않는다.

표준 라이브러리만 사용 (simple_tracker.py와 동일 원칙) — Pi에 새 의존성 추가 불필요.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

_VALID_BACKENDS = {"auto", "picamera2", "opencv"}
_VALID_INFERENCE_BACKENDS = {"auto", "edgetpu", "tflite", "pytorch"}
_VALID_ROTATIONS = {0, 90, 180, 270}
_RESERVED_PORTS = {5000}  # roi_editor 웹 UI 포트

# 카메라별로 다른 학습/해상도 조합의 모델을 고를 수 있게 하는 매핑 — weights 폴더
# 경로 하나와 입력 해상도 하나를 한 번에 고르게 해서, 둘이 어긋나는(예: v3_320
# 가중치인데 input_size=640) 설정 실수를 원천 차단한다. 새 모델을 추가하려면
# 이 dict에 항목만 추가하면 CameraProfile.model_variant로 바로 선택 가능해진다.
MODEL_VARIANTS = {
    "v2_640": {
        "weights_dir": "runs/white_cane_v2/weights",
        "input_size": 640,
        "label": "white_cane_v2 (640, 정확도 우선)",
    },
    "v3_320": {
        "weights_dir": "runs/white_cane_v3_320/weights",
        "input_size": 320,
        "label": "white_cane_v3_320 (320, 속도 우선)",
    },
}
_DEFAULT_MODEL_VARIANT = "v2_640"


@dataclass
class CameraProfile:
    id: str
    enabled: bool = True
    label: str = ""
    backend: str = "auto"            # "auto" | "picamera2" | "opencv"
    source: str = "0"
    rotation: int = 0                # 0 | 90 | 180 | 270
    inference_backend: str = "auto"  # "auto" | "edgetpu" | "tflite" | "pytorch"
    roi_config: str = "rois.json"
    port: int = 8080
    traffic_db: str = "foot_traffic.db"
    swap_rb: bool = False             # 이 카메라의 캡처 경로가 R/B 채널이 뒤바뀌어 나올 때 보정
    # (실측: picamera2 "RGB888" 포맷이 CSI 센서에서는 이미 BGR 순서로 나와 그대로 써도
    # 되지만, libcamera의 uvcvideo(USB UVC) 경로를 타는 카메라는 그렇지 않을 수 있음 —
    # 카메라 하드웨어/드라이버 조합에 따라 달라 자동 판별 대신 카메라별 토글로 둔다.)
    model_variant: str = _DEFAULT_MODEL_VARIANT  # MODEL_VARIANTS의 키 — 카메라별로 다른
    # 모델(해상도/정확도-속도 트레이드오프)을 고를 수 있게 한다.


def load_camera_config(path: str | Path) -> list[CameraProfile]:
    """camera_config.json을 읽어 CameraProfile 목록을 반환. 파일이 없거나 읽기 실패 시 빈 목록."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    profiles = []
    for item in data.get("cameras", []):
        try:
            profiles.append(CameraProfile(
                id=item["id"],
                enabled=item.get("enabled", True),
                label=item.get("label", ""),
                backend=item.get("backend", "auto"),
                source=str(item.get("source", "0")),
                rotation=int(item.get("rotation", 0)),
                inference_backend=item.get("inference_backend", "auto"),
                roi_config=item.get("roi_config", "rois.json"),
                port=int(item.get("port", 8080)),
                traffic_db=item.get("traffic_db", "foot_traffic.db"),
                swap_rb=bool(item.get("swap_rb", False)),
                model_variant=item.get("model_variant", _DEFAULT_MODEL_VARIANT),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return profiles


def save_camera_config(path: str | Path, profiles: list[CameraProfile]) -> None:
    """원자적 쓰기 — roi_editor/server.py의 _save()와 동일 패턴 (동시 읽기에 안전)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"cameras": [asdict(p) for p in profiles]}
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def validate_camera_config(profiles: list[CameraProfile]) -> list[str]:
    """설정 유효성을 검사해 에러 메시지 목록을 반환 (빈 목록이면 유효)."""
    errors: list[str] = []

    ids_seen: dict[str, int] = {}
    ports_seen: dict[int, str] = {}
    edgetpu_ids: list[str] = []

    for p in profiles:
        if p.rotation not in _VALID_ROTATIONS:
            errors.append(f"'{p.id}': rotation 값이 잘못됨 ({p.rotation}) — 0/90/180/270만 허용")
        if p.backend not in _VALID_BACKENDS:
            errors.append(f"'{p.id}': backend 값이 잘못됨 ({p.backend})")
        if p.inference_backend not in _VALID_INFERENCE_BACKENDS:
            errors.append(f"'{p.id}': inference_backend 값이 잘못됨 ({p.inference_backend})")
        if p.model_variant not in MODEL_VARIANTS:
            errors.append(f"'{p.id}': model_variant 값이 잘못됨 ({p.model_variant}) — "
                          f"{list(MODEL_VARIANTS)} 중 하나여야 함")

        ids_seen[p.id] = ids_seen.get(p.id, 0) + 1

        if p.enabled:
            if p.port in _RESERVED_PORTS:
                errors.append(f"'{p.id}': port {p.port}는 roi_editor 웹 UI가 사용 중 — 다른 포트를 지정하세요")
            if p.port in ports_seen:
                errors.append(f"'{p.id}': port {p.port}가 '{ports_seen[p.port]}'와 중복됨")
            else:
                ports_seen[p.port] = p.id

            # "auto"는 build_backend()에서 항상 Coral EdgeTPU부터 시도한다(있으면) —
            # 즉 edgetpu 컴파일 모델이 배포된 상태에서는 "auto"가 사실상 "edgetpu"와
            # 동일하게 동작한다. 이걸 집계에서 빼면, 한쪽은 명시적 edgetpu이고 다른
            # 한쪽은 auto인 카메라 2대가 동시에 물리 Coral USB 동글을 열려다 충돌해서
            # 둘 다 죽는 사고가 난다 — 실제로 겪었다(USB transfer error로 한쪽
            # EdgeTPU 워커가 예기치 않게 종료, 그 카메라는 재시작 전까지 복구 안 됨).
            if p.inference_backend in ("auto", "edgetpu"):
                edgetpu_ids.append(p.id)

    for dup_id, count in ids_seen.items():
        if count > 1:
            errors.append(f"카메라 id '{dup_id}'가 {count}번 중복됨")

    if len(edgetpu_ids) > 1:
        errors.append(
            "물리 Coral 액셀러레이터는 1개뿐이라 inference_backend가 'edgetpu'이거나 "
            "'auto'(Coral을 우선 시도함)인 카메라를 동시에 2개 이상 활성화할 수 없음 — "
            f"나머지 카메라는 'tflite' 또는 'pytorch'로 명시 지정하세요: {edgetpu_ids}"
        )

    return errors
