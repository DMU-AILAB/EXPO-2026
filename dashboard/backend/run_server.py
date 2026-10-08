"""run_server.py — 대시보드 서버 진입점. 콘솔이 없는 환경(Windows `pythonw`, 작업 스케줄러)에서도 죽지 않는다.

`pythonw`로 `python -m uvicorn`을 띄우면 `sys.stdout`/`sys.stderr`가 `None`이라 uvicorn의 로그 설정이 시작 직후
죽는다(`sys.stderr.isatty()`를 부른다). 창도 숨겨져 있어 **아무 메시지 없이 서버가 안 뜬다** — 새 Windows PC의
setup-server가 "60초 안에 응답하지 않습니다"로 실패한 원인이었다(같은 앱을 `python.exe`로 띄우면 정상).

`--log-file`을 주면 stdout·stderr를 그 파일로 돌린다. 시작 실패의 트레이스백도 거기 남는다. 주지 않으면 콘솔
그대로라 개발 중에는 평소처럼 쓴다. `--reload`/`--workers`는 쓰지 않는다 — 하트비트 메모리 버퍼와 APScheduler가
한 프로세스 안에 있다.

    python run_server.py --port 8000                          # 개발: 콘솔에 로그
    pythonw run_server.py --port 8000 --log-file data/logs/server.log   # 설치 스크립트
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_LOG_BYTES = 5 * 1024 * 1024


def rotate(path: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    """로그가 너무 커지면 `.1`로 넘기고 새로 시작한다(자동 시작 서버의 로그가 무한히 자라지 않게)."""
    try:
        if path.is_file() and path.stat().st_size > max_bytes:
            backup = path.with_name(path.name + ".1")
            backup.unlink(missing_ok=True)
            path.replace(backup)
    except OSError:
        pass                                           # 로그 정리 실패가 서버 시작을 막으면 안 된다


def redirect_output(log_file: Path):
    """stdout·stderr를 `log_file`로 돌린다. 한 줄씩 바로 써서(`buffering=1`) 죽기 직전의 메시지도 남는다."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    rotate(log_file)
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    return stream


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VisionGuide 대시보드 서버")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log-file", type=Path, default=None, help="stdout·stderr를 이 파일로 돌린다(콘솔 없는 실행용)")
    ap.add_argument("--app", default="app.main:app", help=argparse.SUPPRESS)       # 테스트용
    args = ap.parse_args(argv)

    os.chdir(HERE)                                     # .env와 ./data가 이 폴더 기준이다
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    if args.log_file is not None:
        redirect_output(args.log_file if args.log_file.is_absolute() else HERE / args.log_file)
        print(f"=== 서버 시작 {time.strftime('%Y-%m-%d %H:%M:%S')} (python {sys.version.split()[0]}, "
              f"{args.host}:{args.port}) ===", flush=True)
    try:
        import uvicorn
        uvicorn.run(args.app, host=args.host, port=args.port, timeout_graceful_shutdown=3)
        return 0
    except SystemExit as exc:                          # uvicorn이 포트 사용 중 등으로 종료할 때
        return int(exc.code or 0) if isinstance(exc.code, int) or exc.code is None else 1
    except BaseException:                              # noqa: BLE001 — 어떤 실패든 파일에 남긴다
        traceback.print_exc()
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    sys.exit(main())
