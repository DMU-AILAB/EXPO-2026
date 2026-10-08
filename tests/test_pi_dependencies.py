"""Pi에 설치돼야 하는 패키지 — **선언 목록 · 코드의 실제 import · 기기 점검**이 서로 맞는지.

`bleak`이 기기에 빠져 있던 일(ESP32 BLE 중계가 "Pi 패키지가 없습니다"로 멈춤)이 두 겹의 문제였다.
1) 선언 목록(`install.sh`·Makefile·`requirements-pi.txt`)이 서로 어긋나 있었고(`requirements-pi.txt`에는 gpiozero·lgpio가
   없다), 새 import가 생겨도 목록에 넣었는지 확인하는 장치가 없었다 — 여기서 정적으로 대조한다.
2) **푸시 업데이트는 코드만 올리고 pip를 실행하지 않는다** — 그래서 선언이 맞아도 기기에는 빠질 수 있다. 기기가
   스스로 점검하고(`diagnose.check_python_modules`) 대시보드 진단이 알린다.
"""

import ast
import re
import shlex
import sys
from pathlib import Path

import diagnose as dg

ROOT = Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")


def _norm(spec: str) -> str:
    """`uvicorn[standard]>=0.20.0` → `uvicorn`, `ai_edge_litert` → `ai-edge-litert`."""
    name = re.split(r"[\[<>=!~ ]", spec.strip(), maxsplit=1)[0]
    return name.lower().replace("_", "-")


def _install_pip() -> set[str]:
    m = re.search(r"^PIP_PACKAGES=\((.*?)\)\s*$", INSTALL, re.M | re.S)
    return {_norm(x) for x in shlex.split(m.group(1))}


def _requirements() -> set[str]:
    out = set()
    for line in (ROOT / "requirements-pi.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(_norm(line))
    return out


def test_진단의_필수_목록이_install_sh와_같다():
    assert {_norm(pkg) for pkg, _ in dg.REQUIRED_MODULES.values()} == _install_pip()


def test_requirements_pi가_install_sh와_같다():
    assert _requirements() == _install_pip()


# --- 실제 import ---------------------------------------------------------------------------

# 필수는 아니지만 코드가 import하는 서드파티 — 이유를 적어 둔다. 새 모듈은 여기 또는 REQUIRED_MODULES에 넣어야 한다.
OPTIONAL = {
    "picamera2": "apt python3-picamera2 (pip로 설치하지 않는다)",
    "libcamera": "apt python3-picamera2에 딸려 온다",
    "pygame": "오디오 재생 폴백(mpg123이 우선) — try/except로 감싸 있다",
    "pydantic": "fastapi의 의존성",
    "tflite_runtime": "Coral용 Python 3.9 서브프로세스(`make install-edgetpu-py39`)와 폴백",
    "tensorflow": "TFLite 폴백 — Pi에는 설치하지 않는다",
    "torch": "PyTorch 폴백 — Pi에는 설치하지 않는다",
    "ultralytics": "PyTorch 폴백 — Pi에는 설치하지 않는다",
}


def _pi_runtime_files():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"^DEPLOY_PY\s*=\s*((?:[^\n\\]*\\\n)*[^\n]*)", mk, re.M)
    files = [ROOT / x for x in re.findall(r"device/[\w.]+\.py", m.group(1))]
    files += list((ROOT / "apps" / "roi_editor").rglob("*.py"))
    files.append(ROOT / "apps" / "simulator" / "roi_manager.py")
    return files


def _third_party_imports() -> dict[str, set[str]]:
    local = ({p.stem for p in (ROOT / "device").glob("*.py")} | {p.stem for p in (ROOT / "apps" / "roi_editor").glob("*.py")}
             | {"simulator", "roi_manager", "server"})
    std = set(sys.stdlib_module_names) | {"__future__"}
    found: dict[str, set[str]] = {}
    for f in _pi_runtime_files():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8-sig"))):          # 일부 파일은 BOM이 있다
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if top not in std and top not in local:
                    found.setdefault(top, set()).add(f.relative_to(ROOT).as_posix())
    return found


def test_코드가_import하는_서드파티는_전부_분류돼_있다():
    """새 서드파티 import를 추가하면서 설치 목록에 넣지 않으면 기기에서 그 기능만 조용히 멈춘다(bleak)."""
    known = set(dg.REQUIRED_MODULES) | set(OPTIONAL) | {"python_multipart"}
    unknown = {m: sorted(fs) for m, fs in _third_party_imports().items() if m not in known}
    assert not unknown, (f"설치 목록에 없는 서드파티 import: {unknown} — deploy/install.sh PIP_PACKAGES, Makefile deps, "
                         "requirements-pi.txt, diagnose.REQUIRED_MODULES에 넣거나 이 파일의 OPTIONAL에 이유와 함께 적으세요")


def test_필수_목록에_쓰이지_않는_항목은_없다():
    used = set(_third_party_imports())
    # PIL은 한글 오버레이에서 지연 import하므로 정적으로 안 보일 수 있다 — 나머지는 전부 코드가 써야 한다
    unused = set(dg.REQUIRED_MODULES) - used - {"PIL", "lgpio", "multipart", "uvicorn"}
    assert not unused, f"코드에서 쓰지 않는 필수 항목: {unused}"


def test_Makefile_deps가_install_sh와_같다():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    pip = set()
    for target in ("deps", "deps-roi-editor"):
        recipe = re.search(rf"^{target}:\n((?:\t.*\n)+)", mk, re.M).group(1)
        m = re.search(r"install --break-system-packages -q ([^\"]+)\"", recipe)
        pip |= {_norm(x) for x in shlex.split(m.group(1))}
    assert pip == _install_pip()


# --- check_python_modules 동작 ------------------------------------------------------------

def _finder(installed: set[str]):
    return lambda name: object() if name in installed else None


def test_전부_있으면_누락이_없다():
    installed = set(dg.REQUIRED_MODULES) | {"python_multipart"}
    assert dg.check_python_modules(_finder(installed)) == {"checked": len(dg.REQUIRED_MODULES), "missing": []}


def test_빠진_모듈을_패키지와_기능과_함께_알린다():
    installed = set(dg.REQUIRED_MODULES) - {"bleak"}
    res = dg.check_python_modules(_finder(installed))
    assert res["missing"] == [{"module": "bleak", "package": "bleak", "feature": "ESP32 BLE 중계"}]


def test_여러_개가_빠져도_모두_낸다():
    res = dg.check_python_modules(_finder({"numpy"}))
    assert {m["module"] for m in res["missing"]} == set(dg.REQUIRED_MODULES) - {"numpy"}


def test_대체_이름만_있어도_설치된_것으로_본다():
    # python-multipart는 새 버전에서 import 이름이 python_multipart다
    installed = (set(dg.REQUIRED_MODULES) - {"multipart"}) | {"python_multipart"}
    assert dg.check_python_modules(_finder(installed))["missing"] == []


def test_find_spec이_예외를_던져도_누락으로_본다():
    def boom(name):
        raise ValueError("bad")
    assert len(dg.check_python_modules(boom)["missing"]) == len(dg.REQUIRED_MODULES)


def test_import하지_않고_찾기만_한다():
    """cv2·ai_edge_litert를 불러오면 느리고 메모리를 쓴다 — find_spec만 쓴다."""
    seen = []
    dg.check_python_modules(lambda name: seen.append(name))
    assert set(dg.REQUIRED_MODULES) <= set(seen)                      # 전부 find_spec으로만 물었다
    src = (ROOT / "device" / "diagnose.py").read_text(encoding="utf-8")
    assert "import_module" not in src and "__import__" not in src
