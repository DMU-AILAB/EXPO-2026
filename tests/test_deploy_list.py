"""`Makefile`의 `DEPLOY_PY`가 `device/`와 정확히 일치하는지 검증한다.

**왜 테스트로 막는가.** 둘이 어긋나면 기기에서 `ImportError`가 나거나, 더 나쁘게는
**구버전 파일이 조용히 남는다**(`CLAUDE.md` 디렉터리 배치 규칙). 새 모듈을 `device/`에
넣고 `DEPLOY_PY`에 추가하는 것을 잊는 실수는 실제로 반복되기 쉬운데, 증상이 기기에서만
나타나므로 로컬 테스트로는 걸리지 않았다.

`CLAUDE.md`의 DEPLOY_PY 표도 같은 이유로 낡았다 — 손으로 관리하다 **25개 중 12개만**
적힌 상태였다. 그래서 이 테스트는 **`Makefile`을 단일 출처로 삼고** 문서가 아니라
파일 시스템과 대조한다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _deploy_py() -> set[str]:
    """Makefile의 DEPLOY_PY에 나열된 device/ 파일명."""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"DEPLOY_PY\s*[:+]?=\s*((?:[^\n\\]*\\\n)*[^\n]*)", mk)
    assert m, "Makefile에서 DEPLOY_PY를 찾지 못했다"
    return set(re.findall(r"device/([A-Za-z_0-9]+\.py)", m.group(1)))


def _device_py() -> set[str]:
    return {p.name for p in (ROOT / "device").iterdir() if p.suffix == ".py"}


def test_deploy_py가_device_디렉터리와_일치한다():
    listed, actual = _deploy_py(), _device_py()
    missing = actual - listed          # 만들었는데 배포 목록에 없다 → 기기에서 ImportError
    extra = listed - actual            # 목록에만 있다 → rsync가 실패한다
    assert not missing, f"DEPLOY_PY에 빠진 파일: {sorted(missing)}"
    assert not extra, f"DEPLOY_PY에만 있고 device/에 없는 파일: {sorted(extra)}"


def test_deploy_py_항목이_device_접두사를_갖는다():
    """`device/` 없이 적으면 rsync가 저장소 루트에서 찾다가 실패한다.

    실제로 `static_mask.py`를 추가할 때 접두사를 빠뜨려 이 형태가 됐었다.
    """
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"DEPLOY_PY\s*[:+]?=\s*((?:[^\n\\]*\\\n)*[^\n]*)", mk)
    for token in re.findall(r"(?<![\w/])([A-Za-z_0-9]+\.py)", m.group(1)):
        assert f"device/{token}" in m.group(1), (
            f"'{token}'에 device/ 접두사가 없다 — rsync가 찾지 못한다")


def test_claude_md의_deploy_py_표가_최신이다():
    """문서 표가 낡으면 새 모듈을 추가할 때 참고를 잘못하게 된다."""
    doc = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    row = re.search(r"\| `DEPLOY_PY` \|([^|]*)\|", doc)
    assert row, "CLAUDE.md에서 DEPLOY_PY 행을 찾지 못했다"
    documented = set(re.findall(r"`([A-Za-z_0-9]+\.py)`", row.group(1)))
    missing = _device_py() - documented
    assert not missing, f"CLAUDE.md DEPLOY_PY 표에 빠진 파일: {sorted(missing)}"
