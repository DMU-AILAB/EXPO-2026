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
                   "[9] 서버 등록", "아무것도 바꾸지 않았습니다"):
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
