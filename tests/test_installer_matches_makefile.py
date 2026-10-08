"""`deploy/install.sh`가 `Makefile`과 어긋나지 않는지.

설치 스크립트는 `make install-service`/`deps`가 하는 일을 Pi 위에서 SSH 없이 하는 것이다.
두 곳에 같은 목록이 있으면 한쪽만 고쳐져 기기에서 패키지나 유닛이 빠진다 — 실제로 이 저장소의
`DEPLOY_PY` 표가 그렇게 낡았었다(12개만 적혀 있었다). 유닛·sudoers 목록은 서버가 Makefile의
scp 목록으로 에셋을 만들므로 어긋날 수 없고, 패키지 목록만 대조하면 된다.
"""

import os
import re
import shlex
import subprocess

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "deploy" / "install.sh"
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def _array(name: str) -> set[str]:
    m = re.search(rf"^{name}=\((.*?)\)\s*$", INSTALL.read_text(encoding="utf-8"), re.M | re.S)
    assert m, f"install.sh에서 {name}를 찾지 못했다"
    return set(shlex.split(m.group(1)))


def _makefile_recipe(target: str) -> str:
    m = re.search(rf"^{target}:\n((?:\t.*\n)+)", MAKEFILE, re.M)
    assert m, f"Makefile에서 {target}를 찾지 못했다"
    return m.group(1)


def test_apt_packages_match_makefile():
    deps = _makefile_recipe("deps")
    apt = set(re.search(r"apt-get install -y ([^|\"&]+?)(?: \|\||\")", deps).group(1).split())
    # iptables는 install-service 쪽에 있다
    svc = _makefile_recipe("install-service")
    apt |= set(re.search(r"apt-get install -y uhubctl ([\w-]+)", svc).group(1).split()) | {"uhubctl"}
    assert _array("APT_PACKAGES") == apt


def _pip_args(recipe: str) -> set[str]:
    m = re.search(r"\) install --break-system-packages -q ([^\"]+)\"", recipe)    # $(PI_PIP) install …
    assert m, "Makefile에서 pip install 줄을 찾지 못했다"
    return set(shlex.split(m.group(1)))


def test_pip_packages_match_makefile():
    assert _array("PIP_PACKAGES") == _pip_args(_makefile_recipe("deps")) | _pip_args(_makefile_recipe("deps-roi-editor"))


def test_install_service_scp_list_is_what_the_server_ships():
    """서버가 만드는 에셋은 이 목록 그대로다 — 유닛을 enable --now 하는 이름도 거기서 온다."""
    from importlib import import_module
    import sys
    sys.path.insert(0, str(ROOT / "dashboard" / "backend"))
    try:
        mod = import_module("app.services.bundle_builder")
        names = mod._install_service_files(MAKEFILE)
    finally:
        sys.path.remove(str(ROOT / "dashboard" / "backend"))
    assert {n for n in names if n.endswith(".service")} >= {
        "visionguide-device.service", "visionguide-roi-editor.service", "visionguide-controls.service"}
    assert "auto_ap.sh" in names


def test_install_script_syntax_and_dry_run(tmp_path):
    assert subprocess.run(["bash", "-n", str(INSTALL)]).returncode == 0
    env = {**os.environ, "HOME": str(tmp_path)}
    res = subprocess.run(["bash", str(INSTALL), "--dry-run", "--server", "http://10.0.0.1:8001", "--token", "T"],
                         capture_output=True, text=True, env=env)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    for needle in ("[1] 사전 점검", "[4] 코드 적용", "visudo -cf", "self_update.py apply",
                   "] 서버 등록", "아무것도 바꾸지 않았습니다"):
        assert needle in out, needle
    # dry-run은 어떤 것도 만들지 않는다
    assert list(tmp_path.iterdir()) == []


def test_dry_run_without_token_skips_registration_but_still_plans(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    res = subprocess.run(["bash", str(INSTALL), "--dry-run", "--no-register", "--server", "http://10.0.0.1:8001"],
                         capture_output=True, text=True, env=env)
    assert res.returncode == 0 and "건너뜁니다" in res.stdout


def test_real_run_refuses_to_run_without_token(tmp_path):
    """토큰 없이는 코드를 받을 수 없다 — dry-run이 아니면 시작하기 전에 멈춘다."""
    env = {**os.environ, "HOME": str(tmp_path)}
    res = subprocess.run(["bash", str(INSTALL), "--server", "http://10.0.0.1:8001"],
                         capture_output=True, text=True, env=env, timeout=30)
    assert res.returncode != 0 and "토큰" in res.stderr


def test_script_refuses_root():
    # root(uid 0)에서는 설치를 거부한다 — 서비스를 돌릴 일반 사용자로 실행해야 한다.
    text = INSTALL.read_text(encoding="utf-8")
    assert '[[ "$(id -u)" -ne 0 ]] || die' in text


# --- 모델 범위 · 핫스팟 프로필 ---------------------------------------------------

def _dry(tmp_path, *args, path_prefix=None):
    env = {**os.environ, "HOME": str(tmp_path), "APP_DIR": str(tmp_path / "visionguide")}
    if path_prefix:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(INSTALL), "--dry-run", "--user", "tester", "--server", "http://10.0.0.1:8001", *args],
        capture_output=True, text=True, env=env, timeout=30)


def _fake_nmcli(tmp_path, profiles: str):
    """`nmcli -t -f NAME connection show`가 `profiles`를 돌려주는 가짜 nmcli."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    exe = bin_dir / "nmcli"
    exe.write_text(f"#!/bin/sh\nprintf '%s' '{profiles}'\n", encoding="utf-8")
    exe.chmod(0o755)
    return str(bin_dir)


def test_bootstrap_model_dirs_match_the_server_bundle():
    """설치 스크립트의 '이미 있으면 받지 않는다' 판정 경로가 서버가 싣는 기본 모델과 같다."""
    import sys
    sys.path.insert(0, str(ROOT / "dashboard" / "backend"))
    try:
        from app.services import bundle_builder as bb
        dirs = bb._model_variant_dirs(ROOT)
        expected = {dirs[k] for k in bb.BOOTSTRAP_MODELS}
    finally:
        sys.path.remove(str(ROOT / "dashboard" / "backend"))
    assert _array("BOOTSTRAP_MODEL_DIRS") == expected


def test_ap_ip_matches_auto_ap_script():
    install = INSTALL.read_text(encoding="utf-8")
    auto_ap = (ROOT / "deploy" / "auto_ap.sh").read_text(encoding="utf-8")
    ip = lambda text: re.search(r'^AP_IP="([\d.]+)"', text, re.M).group(1)
    assert ip(install) == ip(auto_ap)


def test_ap_connection_name_matches_runtime_code():
    # 코드가 `nmcli connection up <이름>`으로 전환하는 이름과 같아야 폴백이 동작한다.
    # import하지 않고 소스를 읽는다 — gpio_controls는 하드웨어 라이브러리에 기대고,
    # network_manager는 conftest 경로 밖(apps/roi_editor)에 있다.
    def const(path: str, name: str) -> str:
        text = (ROOT / path).read_text(encoding="utf-8")
        return re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.M).group(1)

    ours = re.search(r'^AP_CONNECTION="([^"]+)"', INSTALL.read_text(encoding="utf-8"), re.M).group(1)
    assert ours == const("apps/roi_editor/network_manager.py", "AP_CONNECTION")
    assert ours == const("device/gpio_controls.py", "HOTSPOT_CONNECTION")


def test_dry_run_plans_ap_profile_when_missing(tmp_path):
    res = _dry(tmp_path, "--ap-password", "secret123",
               path_prefix=_fake_nmcli(tmp_path, "홈와이파이\nlo\n"))
    assert res.returncode == 0, res.stderr
    assert "nmcli connection add type wifi" in res.stdout
    assert "con-name VisionGuide-AP" in res.stdout
    assert "192.168.4.1/24" in res.stdout and "wifi-sec.psk secret123" in res.stdout
    assert "autoconnect no" in res.stdout            # 평소엔 홈 Wi-Fi, 연결이 없을 때만 auto_ap.sh가 올린다


def test_ap_profile_default_password(tmp_path):
    res = _dry(tmp_path, path_prefix=_fake_nmcli(tmp_path, "lo\n"))
    assert "wifi-sec.psk visionguide" in res.stdout


def test_existing_ap_profile_is_left_alone(tmp_path):
    """기존 기기의 프로필은 SSID·IP가 다를 수 있다 — 있으면 절대 만들지도 바꾸지도 않는다."""
    res = _dry(tmp_path, path_prefix=_fake_nmcli(tmp_path, "VisionGuide-AP\n홈\n"))
    assert res.returncode == 0
    assert "nmcli connection add" not in res.stdout
    assert "이미 있습니다" in res.stdout and "기존 프로필 유지" in res.stdout


@pytest.mark.parametrize("pw", ["short", "x" * 64])
def test_ap_password_length_is_validated_up_front(tmp_path, pw):
    res = _dry(tmp_path, "--ap-password", pw)
    assert res.returncode == 2 and "8~63" in res.stderr


def test_default_models_flag_is_default(tmp_path):
    assert "--models default" in _dry(tmp_path).stdout


@pytest.mark.parametrize("flag", [["--models", "all"], ["--include-models"]])
def test_all_models_and_legacy_alias(tmp_path, flag):
    assert "--models all" in _dry(tmp_path, *flag).stdout


def test_models_none(tmp_path):
    assert "--models none" in _dry(tmp_path, "--models", "none").stdout


def test_invalid_models_value_is_rejected(tmp_path):
    res = _dry(tmp_path, "--models", "everything")
    assert res.returncode == 2 and "none|default|all" in res.stderr


def test_default_models_are_skipped_when_already_installed(tmp_path):
    for d in _array("BOOTSTRAP_MODEL_DIRS"):
        f = tmp_path / "visionguide" / d / "best_int8.tflite"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x")
    # dry-run은 파일을 만들지 않으므로(위 테스트) 여기서는 미리 놓아 둔 것만 있다
    res = _dry(tmp_path)
    assert "기본 모델이 이미 있어 받지 않음" in res.stdout


def test_default_models_are_fetched_when_only_one_is_installed(tmp_path):
    first = sorted(_array("BOOTSTRAP_MODEL_DIRS"))[0]
    f = tmp_path / "visionguide" / first / "best_int8.tflite"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    assert "이미 있어 받지 않음" not in _dry(tmp_path).stdout        # 하나라도 없으면 받는다


def test_all_models_are_always_fetched_even_if_present(tmp_path):
    for d in _array("BOOTSTRAP_MODEL_DIRS"):
        f = tmp_path / "visionguide" / d / "best_int8.tflite"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x")
    assert "이미 있어 받지 않음" not in _dry(tmp_path, "--models", "all").stdout


def test_summary_is_printed(tmp_path):
    out = _dry(tmp_path, path_prefix=_fake_nmcli(tmp_path, "lo\n")).stdout
    assert "요약" in out and "핫스팟 프로필:" in out and "모델:" in out
