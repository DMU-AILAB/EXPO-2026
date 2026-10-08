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

import ast
import gzip
import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

__all__ = ["Bundle", "BundleError", "build_bundle", "build_assets", "installer_script", "repo_root",
           "BOOTSTRAP_MODELS", "MODEL_MODES", "normalize_models"]

# 모델을 번들에 싣는 범위. `none`은 코드만(푸시 업데이트 기본), `default`는 신규 설치에 필요한 만큼만,
# `all`은 Makefile `DEPLOY_MODEL_DIRS` 전부다.
MODEL_MODES = ("none", "default", "all")
# 신규 설치 기본 모델 — 앞이 현행 채택 후보, 뒤가 예비. 키는 `camera_config.MODEL_VARIANTS`의 것이고,
# 가중치 경로는 거기서 읽는다(값을 복제하지 않는다).
BOOTSTRAP_MODELS = ("v15_320", "v10_320")
_MODEL_FILES = ("best_int8.tflite", "best_int8_edgetpu.tflite")


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


def normalize_models(models: "bool | str | None") -> str:
    """`include_models: bool` 호환 — True는 `all`, False/None은 `none`. 문자열은 `MODEL_MODES`만."""
    if models is True:
        return "all"
    if models is False or models is None:
        return "none"
    if models in MODEL_MODES:
        return models
    raise BundleError(f"models는 {'|'.join(MODEL_MODES)} 중 하나여야 합니다: {models!r}")


def _model_variant_dirs(root: Path) -> dict[str, str]:
    """`device/camera_config.py`의 `MODEL_VARIANTS`에서 `{변형 키: weights_dir}`를 읽는다.

    모듈을 import하지 않고 소스에서 리터럴만 읽는다 — `settings.repo_root`가 가리키는 저장소의
    값을 보고, import 부작용(dataclass 등록 등)이 없다.
    """
    src = root / "device" / "camera_config.py"
    if not src.is_file():
        raise BundleError("device/camera_config.py가 없습니다 — 모델 경로를 알 수 없습니다")
    for node in ast.parse(src.read_text(encoding="utf-8")).body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if any(isinstance(t, ast.Name) and t.id == "MODEL_VARIANTS" for t in targets) and node.value is not None:
            try:
                table = ast.literal_eval(node.value)
            except ValueError as exc:
                raise BundleError("MODEL_VARIANTS를 리터럴로 읽지 못했습니다") from exc
            return {k: v["weights_dir"] for k, v in table.items() if isinstance(v, dict) and "weights_dir" in v}
    raise BundleError("camera_config.py에서 MODEL_VARIANTS를 찾지 못했습니다")


def _collect(root: Path, models: "bool | str" = "none") -> tuple[dict[str, Path], dict[str, Path]]:
    """`(코드 파일, 모델 파일)` — 둘 다 `{번들 안 경로: 디스크 경로}`."""
    mode = normalize_models(models)
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

    model_files: dict[str, Path] = {}
    if mode != "none":
        if mode == "all":
            dirs = _makefile_list(text, "DEPLOY_MODEL_DIRS")
        else:
            variants = _model_variant_dirs(root)
            missing_keys = [k for k in BOOTSTRAP_MODELS if k not in variants]
            if missing_keys:
                raise BundleError(f"기본 모델 변형이 MODEL_VARIANTS에 없습니다: {missing_keys}")
            dirs = [variants[k] for k in BOOTSTRAP_MODELS]
        for rel in dirs:
            for name in _MODEL_FILES:
                src = root / rel / name
                if src.is_file():                    # EdgeTPU 컴파일본은 없는 디렉터리가 있다
                    model_files[f"{rel}/{name}"] = src
        if mode == "default" and not any(a.endswith("/best_int8.tflite") for a in model_files):
            # 모델이 하나도 없는 번들은 탐지가 안 되는 기기를 조용히 만든다 — 실패로 알린다.
            raise BundleError("기본 모델 가중치(best_int8.tflite)를 찾지 못했습니다: "
                              + ", ".join(BOOTSTRAP_MODELS))
    return code, model_files


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_bundle(models: "bool | str" = "none", include_models: "bool | None" = None) -> Bundle:
    """코드 번들. `models`는 `none|default|all`(bool도 받는다 — True는 `all`).

    `include_models`는 예전 키워드 호출(`build_bundle(include_models=True)`)을 위한 별칭이고,
    주어지면 `models`보다 우선한다.

    `bundle_id`는 코드 파일의 해시라 모델을 얼마나 싣는지와 무관하다 — 기기가 보고한 id와 비교해
    최신 여부를 판정하므로, 모델 때문에 id가 달라지면 푸시 업데이트가 항상 "구버전"으로 보인다.
    """
    root = repo_root()
    code, models = _collect(root, models if include_models is None else include_models)
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
