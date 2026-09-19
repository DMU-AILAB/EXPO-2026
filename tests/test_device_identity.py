"""device_identity 단위 테스트 — 기기 신원 저장/적재.

Pi가 여러 대가 되면서 필요해진 값이다. 값의 주인은 서버이고 Pi는 받아서 보관만 한다.
"""
import json
import os

import pytest

from device_identity import (
    APP_VERSION, DeviceIdentity, clear_identity, default_path, load_identity,
    save_identity,
)


def _ident(**kw):
    base = dict(device_id="cam-hall-01", api_key="vg_live_abc",
                server_url="http://192.168.0.50:8000")
    base.update(kw)
    return DeviceIdentity(**base)


def test_roundtrip(tmp_path):
    p = tmp_path / "device_identity.json"
    save_identity(p, _ident(name="1층 복도", location="복도 동편"))
    got = load_identity(p)
    assert got.device_id == "cam-hall-01"
    assert got.api_key == "vg_live_abc"
    assert got.name == "1층 복도"
    assert got.is_usable() is True


def test_missing_file_returns_none_instead_of_raising(tmp_path):
    """신원이 없다고 탐지·안내가 멈추면 안 된다 — 서버 연동은 부가 기능이다."""
    assert load_identity(tmp_path / "없는파일.json") is None


def test_corrupted_file_returns_none(tmp_path):
    p = tmp_path / "device_identity.json"
    p.write_text("{깨진 json", encoding="utf-8")
    assert load_identity(p) is None


def test_file_without_device_id_is_treated_as_unregistered(tmp_path):
    p = tmp_path / "device_identity.json"
    p.write_text(json.dumps({"api_key": "x"}), encoding="utf-8")
    assert load_identity(p) is None


def test_unknown_fields_are_ignored(tmp_path):
    """서버가 나중에 필드를 추가해도 기기가 죽지 않아야 한다."""
    p = tmp_path / "device_identity.json"
    p.write_text(json.dumps({"device_id": "d1", "api_key": "k",
                             "server_url": "http://s", "미래필드": 123}),
                 encoding="utf-8")
    got = load_identity(p)
    assert got is not None and got.device_id == "d1"


def test_saved_file_is_owner_only(tmp_path):
    """api_key가 들어 있으므로 다른 사용자가 읽으면 안 된다."""
    p = tmp_path / "device_identity.json"
    save_identity(p, _ident())
    assert oct(os.stat(p).st_mode)[-3:] == "600"


def test_no_temp_file_left_behind(tmp_path):
    """원자적 저장 — 쓰다 만 파일이 남으면 기기가 신원을 잃는다."""
    p = tmp_path / "device_identity.json"
    save_identity(p, _ident())
    assert [f.name for f in tmp_path.iterdir()] == ["device_identity.json"]


def test_overwrite_is_allowed(tmp_path):
    """다른 서버로 옮기거나 키를 교체하는 정상 흐름이 있다."""
    p = tmp_path / "device_identity.json"
    save_identity(p, _ident())
    save_identity(p, _ident(device_id="cam-hall-02", server_url="http://other"))
    assert load_identity(p).device_id == "cam-hall-02"


def test_not_usable_without_server_url(tmp_path):
    """서버 주소가 없으면 보낼 곳이 없다 — 등록은 됐지만 전송은 못 한다."""
    assert _ident(server_url="").is_usable() is False


def test_clear(tmp_path):
    p = tmp_path / "device_identity.json"
    save_identity(p, _ident())
    assert clear_identity(p) is True
    assert load_identity(p) is None
    assert clear_identity(p) is False        # 이미 없으면 False


def test_default_path_sits_next_to_rois(tmp_path):
    assert default_path(tmp_path).name == "device_identity.json"
    assert default_path(tmp_path).parent == tmp_path


def test_version_is_declared():
    """기기 탐색(§12)의 `version` 필드를 채우는 유일한 소스다."""
    assert APP_VERSION and all(part.isdigit() for part in APP_VERSION.split("."))
