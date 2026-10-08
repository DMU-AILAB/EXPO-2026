"""`device/self_update.py` — 푸시 업데이트 번들 적용 검증.

번들을 받아 기기에 쓰는 경로라 **"망가뜨리지 않는다"** 가 전부다. 각 테스트는 실패
경로에서 기기의 파일이 그대로인지, 런타임 파일을 건드리지 않는지를 고정한다.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

import self_update as su


def make_bundle(files: dict[str, bytes | str], *, bundle_id: str = "b1",
                tamper: dict[str, str] | None = None, extra_members=None) -> bytes:
    """manifest를 맞춰 넣은 tar.gz. `tamper`는 manifest의 해시를 일부러 틀리게 한다."""
    data = {k: (v.encode() if isinstance(v, str) else v) for k, v in files.items()}
    manifest = {"bundle_id": bundle_id,
                "files": {k: hashlib.sha256(v).hexdigest() for k, v in data.items()}}
    manifest["files"].update(tamper or {})
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        def add(name: str, payload: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        add("manifest.json", json.dumps(manifest).encode())
        for name, payload in data.items():
            add(name, payload)
        for member, payload in (extra_members or []):
            if payload is None:
                tf.addfile(member)
            else:
                tf.addfile(member, io.BytesIO(payload))
    return buf.getvalue()


@pytest.fixture
def dest(tmp_path):
    d = tmp_path / "visionguide"
    d.mkdir()
    (d / "detect.py").write_text("OLD = 1\n")
    (d / "rois.json").write_text('{"rois": []}')
    (d / "camera_config.json").write_text("{}")
    (d / "device_identity.json").write_text('{"device_id": "x"}')
    return d


def apply(bundle, dest, **kw):
    kw.setdefault("smoke", False)
    return su.apply_bundle(bundle, dest, **kw)


def test_적용하면_파일이_바뀌고_버전이_기록된다(dest):
    result = apply(make_bundle({"detect.py": "NEW = 2\n", "gate.py": "X = 1\n"}, bundle_id="v7"), dest)
    assert (dest / "detect.py").read_text() == "NEW = 2\n"
    assert (dest / "gate.py").read_text() == "X = 1\n"
    assert su.read_bundle_version(dest) == "v7"
    assert result["bundle_id"] == "v7" and result["applied"] == 2
    assert su.has_backup(dest)


def test_런타임_파일은_번들에_있어도_건드리지_않는다(dest):
    bundle = make_bundle({
        "detect.py": "NEW = 2\n",
        "rois.json": "{}", "camera_config.json": '{"x":1}', "device_identity.json": "{}",
        "recordings/a.mp4": "zzz", "foot_traffic.db": "db",
    })
    result = apply(bundle, dest)
    assert (dest / "rois.json").read_text() == '{"rois": []}'
    assert (dest / "camera_config.json").read_text() == "{}"
    assert (dest / "device_identity.json").read_text() == '{"device_id": "x"}'
    assert not (dest / "recordings").exists() and not (dest / "foot_traffic.db").exists()
    assert {"rois.json", "recordings/a.mp4", "foot_traffic.db"} <= set(result["skipped"])


@pytest.mark.parametrize("name", ["../evil.py", "/etc/evil.py", "a/../../evil.py", "x\\y.py"])
def test_경로_탈출은_번들_전체를_거부한다(dest, name):
    info = tarfile.TarInfo(name)
    info.size = 1
    bundle = make_bundle({"detect.py": "NEW = 2\n"}, extra_members=[(info, b"x")])
    with pytest.raises(su.UpdateError):
        apply(bundle, dest)
    assert (dest / "detect.py").read_text() == "OLD = 1\n"
    assert not (dest.parent / "evil.py").exists()


def test_심볼릭_링크는_거부한다(dest):
    link = tarfile.TarInfo("link.py")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    bundle = make_bundle({"detect.py": "NEW = 2\n"}, extra_members=[(link, None)])
    with pytest.raises(su.UpdateError):
        apply(bundle, dest)
    assert (dest / "detect.py").read_text() == "OLD = 1\n"


def test_해시가_다르면_거부하고_기기는_그대로다(dest):
    bundle = make_bundle({"detect.py": "NEW = 2\n"}, tamper={"detect.py": "0" * 64})
    with pytest.raises(su.UpdateError, match="sha256"):
        apply(bundle, dest)
    assert (dest / "detect.py").read_text() == "OLD = 1\n"
    assert not su.has_backup(dest)


def test_manifest가_없으면_거부한다(dest):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("detect.py")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"x=1"))
    with pytest.raises(su.UpdateError, match="manifest"):
        apply(buf.getvalue(), dest)


def test_문법_오류가_있으면_하나도_바꾸지_않는다(dest):
    bundle = make_bundle({"detect.py": "NEW = 2\n", "broken.py": "def (:\n"})
    with pytest.raises(su.UpdateError, match="문법"):
        apply(bundle, dest)
    assert (dest / "detect.py").read_text() == "OLD = 1\n"
    assert not (dest / "broken.py").exists()
    assert not list(dest.glob(".update_stage-*"))      # 스테이징 정리


def test_모델은_include_models일_때만_반영한다(dest):
    files = {"detect.py": "N = 1\n", "runs/m1/weights/best_int8.tflite": b"MODEL",
             "runs/m1/weights/best.pt": b"PT"}
    apply(make_bundle(files), dest)
    assert not (dest / "runs").exists()

    apply(make_bundle(files), dest, include_models=True)
    assert (dest / "runs/m1/weights/best_int8.tflite").read_bytes() == b"MODEL"
    assert not (dest / "runs/m1/weights/best.pt").exists()      # best_int8* 만


def test_롤백은_바뀐_파일을_되돌리고_새로_생긴_파일을_지운다(dest):
    apply(make_bundle({"detect.py": "NEW = 2\n"}, bundle_id="v1"), dest)
    apply(make_bundle({"detect.py": "NEWER = 3\n", "added.py": "A = 1\n"}, bundle_id="v2"), dest)
    assert su.read_bundle_version(dest) == "v2"

    result = su.rollback(dest)
    assert (dest / "detect.py").read_text() == "NEW = 2\n"
    assert not (dest / "added.py").exists()
    assert su.read_bundle_version(dest) == "v1"
    assert result == {"restored": 1, "removed": 1}
    assert not su.has_backup(dest)


def test_백업이_없으면_롤백은_UpdateError(dest):
    with pytest.raises(su.UpdateError):
        su.rollback(dest)


def test_임포트가_깨진_roi_editor는_스모크에서_거부한다(dest):
    """py_compile은 통과하지만 임포트에서 터지는 번들 — 재시작 뒤에는 롤백할 수 없다."""
    bundle = make_bundle({
        "detect.py": "NEW = 2\n",
        "roi_editor/server.py": "import module_that_does_not_exist\n",
    })
    with pytest.raises(su.UpdateError, match="시작되지"):
        su.apply_bundle(bundle, dest, smoke=True)
    assert (dest / "detect.py").read_text() == "OLD = 1\n"
    assert not (dest / "roi_editor").exists()


def test_is_allowed_허용_목록():
    ok = ["camera_live_pi.py", "rf_config_example.json", "roi_editor/server.py",
          "roi_editor/static/index.html", "simulator/roi_manager.py"]
    no = ["rois.json", "recordings/x.mp4", "roi_editor/__pycache__/s.pyc",
          "simulator/app.py", "../x.py", "runs/m/weights/best_int8.tflite"]
    assert all(su.is_allowed(p) for p in ok)
    assert not any(su.is_allowed(p) for p in no)
    assert su.is_allowed("runs/m/weights/best_int8.tflite", include_models=True)
