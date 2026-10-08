"""self_update.py — 대시보드가 보낸 코드 번들을 기기에 적용한다.

SSH 없이 대시보드 버튼 한 번으로 Pi 코드를 올리기 위한 Pi 쪽 절반이다. `roi_editor`의
`POST /api/update`가 이 모듈을 부르고, 번들은 백엔드의 `bundle_builder`가 `Makefile`의
`DEPLOY_PY` 목록대로 만든다.

**원칙은 "기기를 망가뜨리지 않는다"이다.** 배포는 현장 기기를 멈출 수 있는 동작이라
다음 순서로 검증한 뒤에야 파일을 바꾼다.

1. 경로 검증 — 절대경로·`..`·심볼릭 링크가 든 번들은 통째로 거부한다.
2. **허용 목록**만 반영한다. 런타임 파일(`rois.json`·`camera_config.json`·
   `device_identity.json`·`recordings/`·`*.db`)은 번들에 들어 있어도 건드리지 않는다.
3. `manifest.json`의 sha256과 맞대어 본다.
4. 스테이징 사본에서 문법 검사 + `roi_editor/server.py --help` 스모크 — 임포트
   실패를 **재시작 전에** 잡는다. 재시작한 뒤에는 `roi_editor` 자신이 죽어 있어
   스스로 롤백할 수 없다.
5. 통과하면 기존 파일을 `.update_backup/`에 옮기고 `os.replace`로 교체한다.
   어느 단계에서든 실패하면 **아무것도 바뀌지 않는다**.

표준 라이브러리만 쓴다 — `device/`의 다른 순수 모듈과 같은 원칙이다.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

__all__ = [
    "UpdateError", "apply_bundle", "rollback", "read_bundle_version",
    "has_backup", "is_allowed",
]

MANIFEST = "manifest.json"
BACKUP_DIR = ".update_backup"
VERSION_FILE = ".bundle_version"
_BACKUP_INDEX = "_index.json"
_STAGE_PREFIX = ".update_stage-"
_SMOKE_TIMEOUT_SEC = 60


class UpdateError(Exception):
    """번들을 적용할 수 없다 — 이 시점까지 기기의 파일은 바뀌지 않았다."""


def is_allowed(rel: str, include_models: bool = False) -> bool:
    """번들 안의 이 경로를 기기에 반영해도 되는가."""
    if "/" not in rel:
        return rel.endswith(".py") or rel == "rf_config_example.json"
    if rel.startswith("roi_editor/"):
        return "__pycache__" not in rel and not rel.endswith(".pyc")
    if rel == "simulator/roi_manager.py":
        return True
    if include_models and rel.startswith("runs/"):
        return fnmatch.fnmatch(PurePosixPath(rel).name, "best_int8*.tflite")
    return False


def _check_name(name: str) -> str:
    p = PurePosixPath(name)
    if (not name or name.startswith(("/", "\\")) or "\\" in name
            or p.is_absolute() or ".." in p.parts):
        raise UpdateError(f"허용되지 않는 경로가 번들에 있습니다: {name!r}")
    return str(p)


def _read_members(tar_bytes: bytes) -> dict[str, bytes]:
    """tar.gz를 메모리로 읽는다. 일반 파일 외(링크·장치)가 있으면 번들 전체를 거부한다."""
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
            for m in tf.getmembers():
                if m.isdir():
                    continue
                rel = _check_name(m.name)
                if not m.isfile():
                    raise UpdateError(f"파일이 아닌 항목이 번들에 있습니다: {m.name!r}")
                fh = tf.extractfile(m)
                files[rel] = fh.read() if fh else b""
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise UpdateError(f"번들을 읽을 수 없습니다: {exc}") from exc
    return files


def _verify(files: dict[str, bytes], include_models: bool) -> tuple[dict, dict[str, bytes], list[str]]:
    if MANIFEST not in files:
        raise UpdateError("번들에 manifest.json이 없습니다")
    try:
        manifest = json.loads(files[MANIFEST].decode("utf-8"))
        expected = manifest["files"]
        bundle_id = str(manifest["bundle_id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError(f"manifest.json이 올바르지 않습니다: {exc}") from exc

    accepted: dict[str, bytes] = {}
    skipped: list[str] = []
    for rel, data in files.items():
        if rel == MANIFEST:
            continue
        if not is_allowed(rel, include_models):
            skipped.append(rel)
            continue
        accepted[rel] = data

    for rel, data in accepted.items():
        digest = hashlib.sha256(data).hexdigest()
        if expected.get(rel) != digest:
            raise UpdateError(f"sha256이 manifest와 다릅니다: {rel}")
    return {"bundle_id": bundle_id}, accepted, skipped


def _write_stage(stage: Path, accepted: dict[str, bytes]) -> None:
    for rel, data in accepted.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _validate_stage(stage: Path, accepted: dict[str, bytes], smoke: bool) -> None:
    for rel in accepted:
        if rel.endswith(".py"):
            try:
                # 바이트코드 파일을 만들지 않고 문법만 본다(py_compile은 cfile을 요구한다).
                compile((stage / rel).read_bytes(), rel, "exec")
            except (SyntaxError, ValueError) as exc:
                raise UpdateError(f"문법 오류로 적용하지 않았습니다 ({rel}): {exc}") from exc
    server = stage / "roi_editor" / "server.py"
    if smoke and server.is_file():
        # 모듈 최상단의 import가 전부 실행된 뒤 argparse가 --help로 끝난다 — 의존 모듈이
        # 빠졌거나 임포트가 깨졌으면 여기서 0이 아닌 코드로 끝난다.
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            res = subprocess.run([sys.executable, str(server), "--help"], capture_output=True,
                                 timeout=_SMOKE_TIMEOUT_SEC, cwd=str(stage), env=env)
        except subprocess.TimeoutExpired as exc:
            raise UpdateError("roi_editor 스모크 검사가 시간 안에 끝나지 않았습니다") from exc
        if res.returncode != 0:
            tail = (res.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            raise UpdateError("roi_editor가 시작되지 않아 적용하지 않았습니다: " + " | ".join(tail))


def _backup_and_replace(dest: Path, stage: Path, accepted: dict[str, bytes]) -> None:
    backup = dest / BACKUP_DIR
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)

    restored: list[str] = []
    created: list[str] = []
    for rel in accepted:
        live = dest / rel
        if live.is_file():
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, saved)
            restored.append(rel)
        else:
            created.append(rel)
    (backup / _BACKUP_INDEX).write_text(
        json.dumps({"restored": restored, "created": created,
                    "bundle_id": read_bundle_version(dest)}, ensure_ascii=False),
        encoding="utf-8")

    for rel in accepted:
        live = dest / rel
        live.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage / rel, live)       # 같은 파일시스템이라 원자적이다


def apply_bundle(tar_bytes: bytes, dest: Path, include_models: bool = False,
                 smoke: bool = True) -> dict:
    """번들을 검증하고 `dest`에 반영한다. 실패하면 `UpdateError`이고 기기는 그대로다."""
    dest = Path(dest)
    files = _read_members(tar_bytes)
    meta, accepted, skipped = _verify(files, include_models)
    if not accepted:
        raise UpdateError("적용할 파일이 번들에 없습니다")

    dest.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=_STAGE_PREFIX, dir=dest))
    try:
        _write_stage(stage, accepted)
        _validate_stage(stage, accepted, smoke)
        _backup_and_replace(dest, stage, accepted)
        (dest / VERSION_FILE).write_text(meta["bundle_id"], encoding="utf-8")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"bundle_id": meta["bundle_id"], "applied": len(accepted), "skipped": sorted(skipped)}


def has_backup(dest: Path) -> bool:
    return (Path(dest) / BACKUP_DIR / _BACKUP_INDEX).is_file()


def read_bundle_version(dest: Path) -> str:
    """마지막으로 적용한 번들 id. 한 번도 안 올렸으면 빈 문자열."""
    try:
        return (Path(dest) / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def rollback(dest: Path) -> dict:
    """직전 `apply_bundle` 이전 상태로 되돌린다. 백업이 없으면 `UpdateError`."""
    dest = Path(dest)
    backup = dest / BACKUP_DIR
    try:
        index = json.loads((backup / _BACKUP_INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("되돌릴 백업이 없습니다") from exc

    for rel in index.get("restored", []):
        live = dest / rel
        live.parent.mkdir(parents=True, exist_ok=True)
        tmp = live.with_name(live.name + ".rollback.tmp")
        shutil.copy2(backup / rel, tmp)
        os.replace(tmp, live)
    for rel in index.get("created", []):
        try:
            (dest / rel).unlink()
        except OSError:
            pass
    previous = index.get("bundle_id", "")
    if previous:
        (dest / VERSION_FILE).write_text(previous, encoding="utf-8")
    else:
        try:
            (dest / VERSION_FILE).unlink()
        except OSError:
            pass
    shutil.rmtree(backup, ignore_errors=True)
    return {"restored": len(index.get("restored", [])), "removed": len(index.get("created", []))}


def _main(argv: list[str] | None = None) -> int:
    """명령줄 — 설치 스크립트(`deploy/install.sh`)가 검증 로직을 셸로 다시 구현하지 않고
    이 모듈을 그대로 쓰게 한다. 결과는 JSON 한 줄, 실패는 종료코드 2(번들 거부)/1(그 밖)."""
    import argparse

    parser = argparse.ArgumentParser(prog="self_update.py", description="코드 번들 적용/상태")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("apply", help="번들을 검증하고 적용한다")
    ap.add_argument("bundle", help="tar.gz 번들 경로")
    ap.add_argument("--dest", required=True, help="적용할 디렉터리(예: ~/visionguide)")
    ap.add_argument("--include-models", action="store_true")
    ap.add_argument("--no-smoke", action="store_true",
                    help="roi_editor 임포트 스모크를 건너뛴다 — 의존성을 설치하기 전의 첫 설치용")
    st = sub.add_parser("status", help="적용된 번들 id")
    st.add_argument("--dest", required=True)
    args = parser.parse_args(argv)

    import json
    try:
        if args.cmd == "status":
            out = {"bundle_id": read_bundle_version(Path(args.dest).expanduser()),
                   "has_backup": has_backup(Path(args.dest).expanduser())}
        else:
            data = Path(args.bundle).expanduser().read_bytes()
            out = apply_bundle(data, Path(args.dest).expanduser(),
                               include_models=args.include_models, smoke=not args.no_smoke)
    except UpdateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
