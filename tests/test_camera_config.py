"""camera_config.py 단위 테스트 — load/save 라운드트립 + validate_camera_config 규칙."""
import sys
from pathlib import Path


from camera_config import (
    CameraProfile,
    adapt_profiles_to_hardware,
    coral_present,
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
    """사람 동반 필수 조건 — 기본값 True, 카메라별로 끄면 왕복 보존."""
    path = tmp_path / "camera_config.json"
    profiles = [CameraProfile(id="cam0"),
                CameraProfile(id="cam1", port=8081, require_person_for_trigger=False)]
    save_camera_config(path, profiles)
    loaded = load_camera_config(path)
    assert loaded[0].require_person_for_trigger is True
    assert loaded[1].require_person_for_trigger is False


def test_legacy_config_without_require_person_field_defaults_true(tmp_path):
    """필드가 없던 예전 camera_config.json은 기본값(True)이 적용된다.

    Pi의 camera_config.json은 rsync 배포 대상이 아니라 필드가 추가돼도 파일이
    갱신되지 않는다 — 그래서 코드 기본값이 실제 배치 동작을 결정한다.
    """
    path = tmp_path / "camera_config.json"
    path.write_text('{"cameras": [{"id": "cam0", "port": 8080}]}', encoding="utf-8")
    assert load_camera_config(path)[0].require_person_for_trigger is True


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


# ── 하드웨어 적응 (--auto-hardware) ──────────────────────────────────────────

def _two_cams(cam1_enabled=False):
    return [
        CameraProfile(id="cam0", port=8080, inference_backend="auto"),
        CameraProfile(id="cam1", port=8081, enabled=cam1_enabled, source="2",
                      inference_backend="tflite"),
    ]


def test_coral_enables_dual_camera_mode_and_passes_validation():
    adapted, notes = adapt_profiles_to_hardware(_two_cams(), coral=True,
                                                source_available=lambda p: True)
    assert [p.enabled for p in adapted] == [True, True]
    assert adapted[0].inference_backend == "edgetpu"
    assert adapted[1].inference_backend == "tflite"
    assert validate_camera_config(adapted) == []
    assert any("듀얼" in n for n in notes)


def test_coral_keeps_single_camera_when_secondary_device_missing():
    adapted, notes = adapt_profiles_to_hardware(_two_cams(cam1_enabled=True), coral=True,
                                                source_available=lambda p: False)
    assert [p.enabled for p in adapted] == [True, False]
    assert adapted[0].inference_backend == "edgetpu"
    assert validate_camera_config(adapted) == []
    assert any("단일" in n for n in notes)


def test_no_coral_forces_single_camera_and_drops_explicit_edgetpu():
    profiles = _two_cams(cam1_enabled=True)
    profiles[0] = CameraProfile(id="cam0", port=8080, inference_backend="edgetpu")
    adapted, _ = adapt_profiles_to_hardware(profiles, coral=False)
    assert [p.enabled for p in adapted] == [True, False]
    assert adapted[0].inference_backend == "auto"  # build_backend가 TFLite로 폴백
    assert validate_camera_config(adapted) == []


def test_adapt_does_not_mutate_input_and_handles_empty():
    profiles = _two_cams()
    adapt_profiles_to_hardware(profiles, coral=True, source_available=lambda p: True)
    assert profiles == _two_cams()
    assert adapt_profiles_to_hardware([], coral=True) == ([], [])


def test_picamera2_secondary_is_trusted_without_device_node():
    profiles = _two_cams()
    profiles[1] = CameraProfile(id="cam1", port=8081, enabled=False, backend="picamera2",
                                source="1", inference_backend="tflite")
    adapted, _ = adapt_profiles_to_hardware(profiles, coral=True)
    assert adapted[1].enabled is True


def _fake_usb(root, vid, pid):
    dev = root / "2-1"
    dev.mkdir()
    (dev / "idVendor").write_text(vid + "\n")
    (dev / "idProduct").write_text(pid + "\n")


def test_coral_present_detects_both_usb_ids(tmp_path):
    for i, (vid, pid) in enumerate([("18d1", "9302"), ("1a6e", "089a")]):
        root = tmp_path / f"usb{i}"
        root.mkdir()
        _fake_usb(root, vid, pid)
        assert coral_present(root, tmp_path / "nodev") is True


def test_coral_present_false_for_other_usb_and_missing_paths(tmp_path):
    _fake_usb(tmp_path, "046d", "082d")
    assert coral_present(tmp_path, tmp_path / "nodev") is False
    assert coral_present(tmp_path / "missing", tmp_path / "missing") is False


def test_coral_present_detects_pcie_apex_node(tmp_path):
    usb = tmp_path / "usb"
    usb.mkdir()
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "apex_0").write_text("")
    assert coral_present(usb, dev) is True


def test_dual_mode_pins_auto_backends_so_cameras_do_not_fight_over_csi():
    adapted, _ = adapt_profiles_to_hardware(_two_cams(), coral=True,
                                            source_available=lambda p: True)
    assert adapted[0].backend == "picamera2"   # 주: CSI
    assert adapted[1].backend == "opencv"      # 보조: USB


def test_dual_mode_respects_explicit_backends():
    profiles = _two_cams()
    profiles[1] = CameraProfile(id="cam1", port=8081, enabled=False, backend="picamera2",
                                source="1", inference_backend="tflite")
    adapted, _ = adapt_profiles_to_hardware(profiles, coral=True)
    assert adapted[0].backend == "auto"        # 보조가 auto가 아니면 건드리지 않는다
    assert adapted[1].backend == "picamera2"
