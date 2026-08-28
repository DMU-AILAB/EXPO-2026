"""camera_config.py 단위 테스트 — load/save 라운드트립 + validate_camera_config 규칙."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from camera_config import (
    CameraProfile,
    load_camera_config,
    save_camera_config,
    validate_camera_config,
)


def test_load_missing_file_returns_empty_list(tmp_path):
    assert load_camera_config(tmp_path / "does_not_exist.json") == []


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "camera_config.json"
    profiles = [
        CameraProfile(id="cam0"),
        CameraProfile(id="cam1", port=8081, rotation=90, backend="opencv", swap_rb=True),
    ]
    save_camera_config(path, profiles)
    loaded = load_camera_config(path)
    assert loaded == profiles
    assert loaded[1].swap_rb is True
    assert loaded[0].swap_rb is False  # 기본값 — 기존 CSI 카메라는 반전 불필요


def test_require_person_for_trigger_round_trip_and_default(tmp_path):
    """사람 동반 필수 조건 — 기본값 False(기존 배치 동작 유지), 켜면 왕복 보존."""
    path = tmp_path / "camera_config.json"
    profiles = [CameraProfile(id="cam0"),
                CameraProfile(id="cam1", port=8081, require_person_for_trigger=True)]
    save_camera_config(path, profiles)
    loaded = load_camera_config(path)
    assert loaded[0].require_person_for_trigger is False
    assert loaded[1].require_person_for_trigger is True


def test_legacy_config_without_require_person_field_defaults_false(tmp_path):
    """필드가 없던 예전 camera_config.json도 그대로 읽혀야 한다(하위호환)."""
    path = tmp_path / "camera_config.json"
    path.write_text('{"cameras": [{"id": "cam0", "port": 8080}]}', encoding="utf-8")
    assert load_camera_config(path)[0].require_person_for_trigger is False


def test_validate_accepts_well_formed_single_camera():
    profiles = [CameraProfile(id="cam0")]
    assert validate_camera_config(profiles) == []


def test_validate_rejects_duplicate_id():
    profiles = [CameraProfile(id="cam0", port=8080), CameraProfile(id="cam0", port=8081)]
    errors = validate_camera_config(profiles)
    assert any("중복" in e for e in errors)


def test_validate_rejects_duplicate_port():
    profiles = [CameraProfile(id="cam0", port=8080), CameraProfile(id="cam1", port=8080)]
    errors = validate_camera_config(profiles)
    assert any("port" in e for e in errors)


def test_validate_rejects_reserved_roi_editor_port():
    profiles = [CameraProfile(id="cam0", port=5000)]
    errors = validate_camera_config(profiles)
    assert any("roi_editor" in e for e in errors)


def test_validate_rejects_bad_rotation():
    profiles = [CameraProfile(id="cam0", rotation=45)]
    errors = validate_camera_config(profiles)
    assert any("rotation" in e for e in errors)


def test_validate_rejects_two_enabled_edgetpu_profiles():
    profiles = [
        CameraProfile(id="cam0", port=8080, inference_backend="edgetpu"),
        CameraProfile(id="cam1", port=8081, inference_backend="edgetpu"),
    ]
    errors = validate_camera_config(profiles)
    assert any("Coral" in e for e in errors)


def test_validate_allows_one_edgetpu_and_one_tflite():
    profiles = [
        CameraProfile(id="cam0", port=8080, inference_backend="edgetpu"),
        CameraProfile(id="cam1", port=8081, inference_backend="tflite"),
    ]
    assert validate_camera_config(profiles) == []


def test_validate_rejects_auto_and_edgetpu_together():
    """실제로 발생한 사고 재현: 한 카메라가 명시적 edgetpu, 다른 카메라가 auto면
    auto가 실행 시 Coral을 먼저 시도해서 물리 동글 하나를 두고 충돌한다."""
    profiles = [
        CameraProfile(id="cam0", port=8080, inference_backend="edgetpu"),
        CameraProfile(id="cam1", port=8081, inference_backend="auto"),
    ]
    errors = validate_camera_config(profiles)
    assert any("Coral" in e for e in errors)


def test_validate_rejects_two_auto_profiles():
    profiles = [
        CameraProfile(id="cam0", port=8080, inference_backend="auto"),
        CameraProfile(id="cam1", port=8081, inference_backend="auto"),
    ]
    errors = validate_camera_config(profiles)
    assert any("Coral" in e for e in errors)


def test_validate_allows_auto_and_tflite_together():
    profiles = [
        CameraProfile(id="cam0", port=8080, inference_backend="auto"),
        CameraProfile(id="cam1", port=8081, inference_backend="tflite"),
    ]
    assert validate_camera_config(profiles) == []


def test_validate_ignores_disabled_profiles_for_port_and_edgetpu_checks():
    profiles = [
        CameraProfile(id="cam0", port=8080, inference_backend="edgetpu", enabled=True),
        CameraProfile(id="cam1", port=8080, inference_backend="edgetpu", enabled=False),
    ]
    assert validate_camera_config(profiles) == []
