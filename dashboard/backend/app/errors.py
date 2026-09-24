"""전역 오류 응답 — 명세 §1.4의 형식과 §16의 코드표를 한 곳에서 강제한다.

라우터마다 `detail={"error": ...}`을 손으로 적으면 빠뜨리는 곳이 생기고, FastAPI가
`HTTPException`을 `{"detail": ...}`로 감싸버려 형식이 두 가지가 된다. 실제로 그랬다 —
프런트는 `error` 코드를 보고 분기해야 하는데 응답에 그 키가 없었다.

    {"error": "ERROR_CODE", "message": "설명", "ok": false}

**상태 코드는 바꾸지 않는다**(5xx를 4xx로 접지 않는다). Pi의 `EventSender`가 4xx를
"다시 보내도 소용없다"로 읽고 이벤트를 버리기 때문에(`device/event_logger.py:193-197`),
서버 내부 오류를 4xx로 내면 그 이벤트가 영구 소실된다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# 상태 코드 → 기본 error 코드 (명세 §16). 라우터가 명시하면 그쪽이 이긴다.
_DEFAULT_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    412: "ETAG_MISMATCH",
    423: "ACCOUNT_LOCKED",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
    502: "DEVICE_UNREACHABLE",
    503: "SERVICE_UNAVAILABLE",
    504: "DEVICE_TIMEOUT",
}


def _envelope(status: int, detail: Any) -> dict:
    """라우터가 넘긴 detail이 dict든 문자열이든 배열이든 한 형식으로 접는다."""
    code = _DEFAULT_CODES.get(status, "INTERNAL_ERROR")
    message: Any = detail

    if isinstance(detail, dict):
        code = detail.get("error", code)
        message = detail.get("message", detail.get("detail", ""))
    elif isinstance(detail, (list, tuple)):
        # Pi의 검증 오류는 문자열 배열로 온다 — 그대로 살려야 무엇이 틀렸는지 보인다.
        message = list(detail)

    return {"error": code, "message": message, "ok": False}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.status_code, exc.detail),
            headers=getattr(exc, "headers", None),   # 429의 Retry-After 등
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        # 명세 §16은 요청 바디 오류를 **400 VALIDATION_ERROR**로 규정한다(FastAPI 기본은 422).
        # 폴리곤 오류는 그중에서도 따로 코드가 있다.
        errors = exc.errors()
        code = "INVALID_POLYGON" if _is_polygon_error(errors) else "VALIDATION_ERROR"
        return JSONResponse(
            status_code=400,
            content={"error": code, "message": _readable(errors), "ok": False},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # 여기서 5xx를 유지하는 것이 중요하다 — Pi가 재시도할 수 있는 유일한 신호다.
        logger.exception("처리되지 않은 오류: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "INTERNAL_ERROR", "message": "서버 내부 오류", "ok": False},
        )


def _is_polygon_error(errors: list[dict]) -> bool:
    return any("polygon" in [str(p) for p in err.get("loc", ())] for err in errors)


def _readable(errors: list[dict]) -> list[str]:
    out = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query"))
        out.append(f"{loc}: {err.get('msg', '')}".strip(": "))
    return out
