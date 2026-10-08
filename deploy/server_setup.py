"""server_setup.py — 대시보드 서버 설치의 **판단 로직** (표준 라이브러리만, OS 무관).

`setup-server.ps1`/`.sh`는 winget·방화벽·작업 스케줄러 같은 OS 일만 하고, `.env`를 어떻게
채울지·관리자 비밀번호를 어디에 둘지는 전부 여기서 정한다. OS 스크립트는 pytest로 돌릴 수
없지만 이 파일은 돌릴 수 있기 때문이다(`tests/test_server_setup.py`).

    python deploy/server_setup.py env --backend dashboard/backend --port 8000 [--reset]
    python deploy/server_setup.py lan-ip
    python deploy/server_setup.py check --port 8000

출력은 모두 JSON 한 덩어리(stdout)이고 사람이 읽을 메시지는 stderr다 — 호출하는 스크립트가
`ConvertFrom-Json`/`json.load`로 바로 읽는다.

★ **멱등이어야 한다.** 설치를 다시 돌렸을 때 기존 `.env`의 값(운영 중 바꾼 JWT 키·포트)을
바꾸면 모든 로그인이 풀리고 기기와 서버 주소가 어긋난다. 그래서 이미 있는 값은 보존하고
빠진 키만 채우며, 덮어쓰기는 `--reset`뿐이다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sys
from pathlib import Path
from typing import Optional

PYTHON_MIN = (3, 10)

# 공개돼 있는 예제 값 — `.env.example`을 그대로 복사해 둔 경우 "설정된 값"으로 보면 안 된다.
_PLACEHOLDERS = {
    "JWT_SECRET_KEY": {"", "changeme_secret_key_in_production"},
    "INITIAL_ADMIN_PASSWORD": {"", "change-me", "admin"},
}

# 비밀번호에서 뺄 글자: 화면에서 읽어 옮겨 적을 때 헷갈리는 것들(0/O/o, 1/l/I).
_PW_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_LEN = 12


def generate_password() -> str:
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(PASSWORD_LEN))


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def parse_env(text: str) -> dict[str, str]:
    """`KEY=VALUE` 줄만 읽는다(주석·빈 줄·형식이 아닌 줄은 무시)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def _set_key(text: str, key: str, value: str) -> str:
    """`key` 줄의 값을 바꾸고, 줄이 없으면 끝에 붙인다. 나머지 줄과 주석은 그대로 둔다."""
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(lambda _m: line, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def _atomic_write(path: Path, content: str, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    if mode is not None:
        try:
            os.chmod(tmp, mode)                 # Windows에서는 의미가 제한적이라 실패해도 계속한다
        except OSError:
            pass
    os.replace(tmp, path)


def _cors(port: int) -> str:
    # 같은 포트로 서빙하면 CORS가 필요 없지만, 개발 서버(vite)로도 열 수 있게 둘 다 허용한다.
    return ",".join([f"http://localhost:{port}", f"http://127.0.0.1:{port}",
                     "http://localhost:5173", "http://127.0.0.1:5173"])


def write_env(backend: Path, port: int = 8000, reset: bool = False) -> dict:
    """`backend/.env`를 만든다/보충한다.

    반환: `{"status": "created"|"updated"|"kept", "env_file", "admin_password"(이번에 새로 만든
    경우만, 아니면 None), "password_file", "port", "filled": [채운 키들]}`.
    `init_db`는 관리자 계정이 **없을 때만** `INITIAL_ADMIN_PASSWORD`를 쓰므로, 이미 계정이 있는 DB에
    새 비밀번호를 만들어도 로그인 비밀번호가 바뀌지는 않는다.
    """
    backend = Path(backend)
    env_path = backend / ".env"
    example_path = backend / ".env.example"
    password_file = backend / "data" / "admin-password.txt"

    existing_text = env_path.read_text(encoding="utf-8") if env_path.is_file() else None
    creating = reset or existing_text is None
    if creating:
        text = example_path.read_text(encoding="utf-8") if example_path.is_file() else ""
        existing: dict[str, str] = {}
    else:
        text = existing_text or ""
        existing = parse_env(text)

    eff_port = port if creating or "PORT" not in existing else _to_int(existing["PORT"], port)

    def needs(key: str) -> bool:
        if creating:
            return True
        if key not in existing:
            return True
        return key in _PLACEHOLDERS and existing[key] in _PLACEHOLDERS[key]

    generated_password: Optional[str] = None
    filled: list[str] = []

    wanted = [
        ("PORT", lambda: str(eff_port)),
        ("PUBLIC_BASE_URL", lambda: "auto"),
        ("AUTO_ENROLL", lambda: "true"),
        ("CORS_ORIGINS", lambda: _cors(eff_port)),
        ("JWT_SECRET_KEY", generate_secret),
        ("INITIAL_ADMIN_PASSWORD", generate_password),
    ]
    for key, make in wanted:
        if not needs(key):
            continue
        value = make()
        if key == "INITIAL_ADMIN_PASSWORD":
            generated_password = value
        text = _set_key(text, key, value)
        filled.append(key)

    if filled:
        _atomic_write(env_path, text, mode=0o600)
    if generated_password is not None:
        _atomic_write(password_file, generated_password + "\n", mode=0o600)

    status = "created" if creating else ("updated" if filled else "kept")
    return {
        "status": status,
        "env_file": str(env_path),
        "admin_password": generated_password,
        "password_file": str(password_file) if password_file.is_file() else None,
        "port": eff_port,
        "filled": filled,
    }


def _to_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def lan_ip() -> Optional[str]:
    """기본 경로로 나가는 인터페이스의 IPv4(요약에 보여 줄 접속 주소용).

    UDP `connect()`는 패킷을 보내지 않고 라우팅만 정한다 — `server_address.local_ip_toward`와
    같은 방식이라 인터넷이 없어도(사설 LAN만 있어도) 동작한다.
    """
    for target in (("192.0.2.1", 9), ("8.8.8.8", 80)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(target)
                ip = s.getsockname()[0]
        except OSError:
            continue
        if ip and ip != "0.0.0.0":
            return ip
    return None


def port_in_use(port: int) -> bool:
    """`0.0.0.0:port`에 바인딩할 수 없으면 True. (포트 0은 OS가 고르므로 항상 False)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=True))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="VisionGuide 서버 설치 도우미")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_env = sub.add_parser("env", help="backend/.env 생성·보충 (멱등)")
    p_env.add_argument("--backend", required=True, help="dashboard/backend 경로")
    p_env.add_argument("--port", type=int, default=8000)
    p_env.add_argument("--reset", action="store_true", help="기존 .env를 덮어쓴다")

    sub.add_parser("lan-ip", help="기본 경로의 LAN IPv4")

    p_chk = sub.add_parser("check", help="파이썬 버전·포트 점검")
    p_chk.add_argument("--port", type=int, default=8000)

    args = ap.parse_args(argv)

    if args.cmd == "env":
        backend = Path(args.backend)
        if not backend.is_dir():
            print(f"[ERROR] backend 디렉터리가 없습니다: {backend}", file=sys.stderr)
            return 2
        _emit(write_env(backend, port=args.port, reset=args.reset))
        return 0

    if args.cmd == "lan-ip":
        _emit({"lan_ip": lan_ip()})
        return 0

    # check
    cur = sys.version_info[:2]
    ok = cur >= PYTHON_MIN
    result = {
        "python": f"{cur[0]}.{cur[1]}.{sys.version_info[2]}",
        "python_ok": ok,
        "python_min": f"{PYTHON_MIN[0]}.{PYTHON_MIN[1]}",
        "port": args.port,
        "port_in_use": port_in_use(args.port),
    }
    _emit(result)
    if not ok:
        print(f"[ERROR] Python {result['python_min']} 이상이 필요합니다 (현재 {result['python']})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
