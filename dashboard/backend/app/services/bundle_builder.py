"""bundle_builder.py — 기기에 올릴 코드 번들(tar.gz)을 만든다.

SSH 없이 대시보드 버튼으로 Pi 코드를 올리는 푸시 업데이트의 서버 쪽 절반이다. Pi는
`device/self_update.py`가 받는다.

**배포 목록을 복제하지 않는다.** 저장소 루트 `Makefile`의 `DEPLOY_PY`(와
`DEPLOY_MODEL_DIRS`)를 읽는다 — `make sync`와 같은 목록이라 둘이 어긋날 수 없고,
`tests/test_deploy_list.py`가 그 목록과 `device/`의 일치를 이미 지킨다.

번들 배치는 Pi의 평면 배치를 따른다: `device/*.py`는 최상위, `apps/roi_editor/`는
`roi_editor/`, `apps/simulator/roi_manager.py`는 `simulator/roi_manager.py`.

`bundle_id`는 **내용 해시**다(코드 파일 기준, 모델 제외). 같은 코드면 언제 만들어도 같은
id라 기기와 서버의 "최신 여부"를 비교할 수 있다.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

__all__ = ["Bundle", "BundleError", "build_bundle", "build_assets", "installer_script", "repo_root"]


class BundleError(Exception):
    """번들을 만들 수 없다 — 저장소가 없거나 배포 목록의 파일이 빠졌다."""


@dataclass(frozen=True)
class Bundle:
    data: bytes
    bundle_id: str
    file_count: int


def repo_root() -> Path:
    if settings.repo_root:
        return Path(settings.repo_root).resolve()
    return Path(__file__).resolve().parents[4]


def _makefile_list(makefile: str, name: str) -> list[str]:
    m = re.search(rf"^{name}\s*[:+]?=\s*((?:[^\n\\]*\\\n)*[^\n]*)", makefile, re.M)
    if not m:
        raise BundleError(f"Makefile에서 {name}를 찾지 못했습니다")
    return re.findall(r"[\w./-]+", m.group(1).replace("\\\n", " "))


def _collect(root: Path, include_models: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    """`(코드 파일, 모델 파일)` — 둘 다 `{번들 안 경로: 디스크 경로}`."""
    mk = root / "Makefile"
    if not mk.is_file():
        raise BundleError(f"저장소 루트에서 Makefile을 찾지 못했습니다: {root}")
    text = mk.read_text(encoding="utf-8")

    code: dict[str, Path] = {}
    for rel in _makefile_list(text, "DEPLOY_PY"):
        src = root / rel
        if not src.is_file():
            raise BundleError(f"DEPLOY_PY의 파일이 없습니다: {rel}")
        code[Path(rel).name] = src

    extra = {"rf_config_example.json": root / "configs/examples/rf_config_example.json",
             "simulator/roi_manager.py": root / "apps/simulator/roi_manager.py"}
    for arc, src in extra.items():
        if not src.is_file():
            raise BundleError(f"번들에 넣을 파일이 없습니다: {src.relative_to(root)}")
        code[arc] = src

    editor = root / "apps/roi_editor"
    if not editor.is_dir():
        raise BundleError("apps/roi_editor가 없습니다")
    for src in sorted(editor.rglob("*")):
        if (src.is_file() and "__pycache__" not in src.parts and src.suffix != ".pyc"):
            code[f"roi_editor/{src.relative_to(editor).as_posix()}"] = src

    models: dict[str, Path] = {}
    if include_models:
        for rel in _makefile_list(text, "DEPLOY_MODEL_DIRS"):
            for name in ("best_int8.tflite", "best_int8_edgetpu.tflite"):
                src = root / rel / name
                if src.is_file():                    # EdgeTPU 컴파일본은 없는 디렉터리가 있다
                    models[f"{rel}/{name}"] = src
    return code, models


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_bundle(include_models: bool = False) -> Bundle:
    root = repo_root()
    code, models = _collect(root, include_models)
    files = {**code, **models}
    hashes = {arc: _sha(src) for arc, src in files.items()}

    code_digest = hashlib.sha256(
        "\n".join(f"{arc}:{hashes[arc]}" for arc in sorted(code)).encode()).hexdigest()
    bundle_id = f"b-{code_digest[:12]}"
    manifest = {"bundle_id": bundle_id, "files": hashes}

    raw = io.BytesIO()
    # mtime을 고정해 같은 내용이면 바이트도 같게 한다(gzip 헤더의 시각도 0).
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            def add(name: str, payload: bytes) -> None:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mtime = 0
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(payload))
            add("manifest.json", json.dumps(manifest, sort_keys=True).encode())
            for arc in sorted(files):
                add(arc, files[arc].read_bytes())
    return Bundle(data=raw.getvalue(), bundle_id=bundle_id, file_count=len(files))


def _install_service_files(makefile: str) -> list[str]:
    """`make install-service`가 Pi로 보내는 `deploy/…` 파일 — 같은 목록을 설치 스크립트도 쓴다.

    `scp deploy/a deploy/b … host:/tmp/` 한 줄이 단일 출처다. 설치 스크립트에 유닛 이름을
    다시 적으면 Makefile과 어긋나 기기에서 유닛이 빠진다(`tests/test_installer_matches_makefile.py`).
    """
    m = re.search(r"^install-service:\n(?:.*\n)*?\tscp ((?:deploy/[\w.-]+\s+)+)", makefile, re.M)
    if not m:
        raise BundleError("Makefile의 install-service에서 scp 목록을 찾지 못했습니다")
    return re.findall(r"deploy/([\w.-]+)", m.group(1))


def build_assets() -> Bundle:
    """설치 스크립트용 에셋 — systemd 유닛·sudoers·`auto_ap.sh`. 평면 배치(`<이름>`)로 담는다."""
    root = repo_root()
    mk = root / "Makefile"
    if not mk.is_file():
        raise BundleError(f"저장소 루트에서 Makefile을 찾지 못했습니다: {root}")
    names = _install_service_files(mk.read_text(encoding="utf-8"))
    files: dict[str, Path] = {}
    for name in names:
        src = root / "deploy" / name
        if not src.is_file():
            raise BundleError(f"deploy/{name}이 없습니다")
        files[name] = src

    hashes = {name: _sha(src) for name, src in files.items()}
    digest = hashlib.sha256("\n".join(f"{n}:{hashes[n]}" for n in sorted(hashes)).encode()).hexdigest()
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            for name in sorted(files):
                payload = files[name].read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mtime = 0
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                tf.addfile(info, io.BytesIO(payload))
    return Bundle(data=raw.getvalue(), bundle_id=f"a-{digest[:12]}", file_count=len(files))


def installer_script(server_url: str) -> str:
    """`deploy/install.sh`에 서버 주소 기본값을 넣어 돌려준다.

    주소는 서버가 만든 값이지만 셸에 들어가므로 URL 문자만 허용한다 — 따옴표나 `;`가 섞이면
    설치 스크립트가 임의 명령을 실행하게 된다.
    """
    if not re.fullmatch(r"https?://[A-Za-z0-9._:\-\[\]]+", server_url):
        raise BundleError(f"서버 주소 형식이 안전하지 않습니다: {server_url!r}")
    path = repo_root() / "deploy" / "install.sh"
    if not path.is_file():
        raise BundleError("deploy/install.sh가 없습니다")
    return path.read_text(encoding="utf-8").replace("__SERVER_URL__", server_url)
