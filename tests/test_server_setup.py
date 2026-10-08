"""deploy/server_setup.py — 서버 설치의 로직 단일 출처.

OS 스크립트(setup-server.ps1/.sh)는 얇게 두고 `.env`·관리자 비밀번호 같은 **판단**은 전부
여기 있다. 그래서 여기가 틀리면 설치마다 다르게 틀린다 — 특히 **멱등성**이 핵심이다.
설치를 두 번 돌렸을 때 기존 `.env`의 값(운영 중 바꾼 JWT 키·포트)이 조용히 바뀌면
모든 로그인이 풀리고 기기와 서버 주소가 어긋난다.
"""

import json
import socket
import sys

import pytest

import server_setup as ss

EXAMPLE = """\
# 서버
HOST=0.0.0.0
PORT=8000

# JWT — 운영에서는 반드시 바꿀 것
JWT_SECRET_KEY=changeme_secret_key_in_production

# CORS
CORS_ORIGINS=http://192.168.0.50:5173

PUBLIC_BASE_URL=auto

INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=change-me
"""


def _backend(tmp_path, example=EXAMPLE):
    b = tmp_path / "backend"
    b.mkdir()
    (b / ".env.example").write_text(example, encoding="utf-8")
    return b


def _env(b):
    return ss.parse_env((b / ".env").read_text(encoding="utf-8"))


def test_새_env는_예제를_바탕으로_비밀을_채운다(tmp_path):
    b = _backend(tmp_path)
    res = ss.write_env(b, port=8000)
    env = _env(b)
    assert res["status"] == "created"
    assert env["JWT_SECRET_KEY"] != "changeme_secret_key_in_production"
    assert len(env["JWT_SECRET_KEY"]) >= 32
    assert env["INITIAL_ADMIN_PASSWORD"] != "change-me"
    assert len(env["INITIAL_ADMIN_PASSWORD"]) == 12
    assert env["PORT"] == "8000"
    assert env["PUBLIC_BASE_URL"] == "auto"
    assert env["AUTO_ENROLL"] == "true"
    assert "http://localhost:8000" in env["CORS_ORIGINS"]
    assert "http://localhost:5173" in env["CORS_ORIGINS"]          # 개발 서버 호환


def test_예제의_주석은_보존된다(tmp_path):
    b = _backend(tmp_path)
    ss.write_env(b, port=8000)
    assert "# JWT — 운영에서는 반드시 바꿀 것" in (b / ".env").read_text(encoding="utf-8")


def test_포트는_인자를_따른다(tmp_path):
    b = _backend(tmp_path)
    ss.write_env(b, port=9000)
    env = _env(b)
    assert env["PORT"] == "9000"
    assert "http://localhost:9000" in env["CORS_ORIGINS"]


def test_비밀번호_파일은_생성_때만_쓴다(tmp_path):
    b = _backend(tmp_path)
    res = ss.write_env(b, port=8000)
    pw_file = b / "data" / "admin-password.txt"
    assert pw_file.read_text(encoding="utf-8").strip() == _env(b)["INITIAL_ADMIN_PASSWORD"]
    assert res["admin_password"] == _env(b)["INITIAL_ADMIN_PASSWORD"]
    assert res["password_file"] == str(pw_file)


def test_두번째_실행은_아무것도_바꾸지_않는다(tmp_path):
    b = _backend(tmp_path)
    ss.write_env(b, port=8000)
    before = (b / ".env").read_text(encoding="utf-8")
    pw_before = (b / "data" / "admin-password.txt").read_text(encoding="utf-8")

    res = ss.write_env(b, port=8000)

    assert res["status"] == "kept"
    assert (b / ".env").read_text(encoding="utf-8") == before
    assert (b / "data" / "admin-password.txt").read_text(encoding="utf-8") == pw_before
    assert res["admin_password"] is None            # 이미 있는 비밀번호는 다시 보여 주지 않는다


def test_기존_값은_보존하고_빠진_키만_채운다(tmp_path):
    b = _backend(tmp_path)
    (b / ".env").write_text(
        "JWT_SECRET_KEY=my-real-secret\nPORT=9100\nPUBLIC_BASE_URL=http://10.0.0.5:9100\n",
        encoding="utf-8")

    res = ss.write_env(b, port=8000)
    env = _env(b)

    assert res["status"] == "updated"
    assert env["JWT_SECRET_KEY"] == "my-real-secret"            # 보존
    assert env["PORT"] == "9100"                                # 인자(8000)보다 기존 값이 우선
    assert env["PUBLIC_BASE_URL"] == "http://10.0.0.5:9100"     # 명시한 주소는 건드리지 않는다
    assert env["AUTO_ENROLL"] == "true"                         # 빠진 키는 채운다
    assert "INITIAL_ADMIN_PASSWORD" in env


def test_플레이스홀더_비밀은_빠진_것으로_본다(tmp_path):
    # .env.example을 그대로 복사해 둔 경우 — 이 값은 공개돼 있어 "설정된 값"이 아니다.
    b = _backend(tmp_path)
    (b / ".env").write_text(EXAMPLE, encoding="utf-8")
    ss.write_env(b, port=8000)
    env = _env(b)
    assert env["JWT_SECRET_KEY"] != "changeme_secret_key_in_production"
    assert env["INITIAL_ADMIN_PASSWORD"] != "change-me"
    assert env["HOST"] == "0.0.0.0"                             # 나머지는 그대로


def test_reset만_덮어쓴다(tmp_path):
    b = _backend(tmp_path)
    ss.write_env(b, port=8000)
    old = _env(b)["JWT_SECRET_KEY"]
    res = ss.write_env(b, port=8000, reset=True)
    assert res["status"] == "created"
    assert _env(b)["JWT_SECRET_KEY"] != old


def test_비밀은_매번_다르다(tmp_path):
    keys = set()
    for i in range(5):
        b = tmp_path / f"b{i}"
        b.mkdir()
        (b / ".env.example").write_text(EXAMPLE, encoding="utf-8")
        ss.write_env(b, port=8000)
        keys.add(_env(b)["JWT_SECRET_KEY"])
    assert len(keys) == 5


def test_관리자_비밀번호는_헷갈리는_글자를_뺀다():
    for _ in range(50):
        pw = ss.generate_password()
        assert len(pw) == 12
        assert not set(pw) & set("0O1lI")


def test_예제가_없어도_최소_env를_만든다(tmp_path):
    b = tmp_path / "backend"
    b.mkdir()
    ss.write_env(b, port=8000)
    env = _env(b)
    assert env["PORT"] == "8000" and env["JWT_SECRET_KEY"] and env["AUTO_ENROLL"] == "true"


def test_parse_env는_주석과_빈줄을_무시한다():
    assert ss.parse_env("# c\n\nA=1\n  B = 2 \nbad line\n") == {"A": "1", "B": "2"}


def test_포트_사용_여부를_감지한다():
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert ss.port_in_use(port) is True
    finally:
        s.close()
    assert ss.port_in_use(port) is False


def test_check는_파이썬_버전과_포트를_보고한다(tmp_path, capsys):
    rc = ss.main(["check", "--port", "0"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["python_ok"] is True
    assert out["python"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}")
    assert "port_in_use" in out


def test_check는_낡은_파이썬이면_실패한다(monkeypatch, capsys):
    monkeypatch.setattr(ss, "PYTHON_MIN", (99, 0))
    rc = ss.main(["check", "--port", "0"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["python_ok"] is False


def test_lan_ip는_주소_또는_None이다():
    ip = ss.lan_ip()
    assert ip is None or ip.count(".") == 3


def test_env_cli는_json을_출력한다(tmp_path, capsys):
    b = _backend(tmp_path)
    rc = ss.main(["env", "--backend", str(b), "--port", "8000"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "created" and out["admin_password"]


def test_backend_디렉터리가_없으면_오류(tmp_path, capsys):
    rc = ss.main(["env", "--backend", str(tmp_path / "nope"), "--port", "8000"])
    assert rc == 2
