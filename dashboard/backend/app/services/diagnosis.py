"""diagnosis.py — 기기 한 대의 연결 상태를 점검해 **원인과 해결 방법**을 낸다.

이번 세션에서 가장 오래 걸린 일은 "왜 안 되지"를 찾는 것이었다 — 방화벽(기기→서버만 막힘),
구버전 코드(키 없는 인수 미지원), 서버 주소 불일치, `cam1` 미연결("주의"), 전력 의심.
각 점검이 그 원인 하나에 대응한다.

점검은 서로 독립이다 — 하나가 실패해도 나머지는 계속한다. 기기에 닿지 않으면 기기에 물어야
하는 항목은 `unknown`으로 두고 이유를 적는다(닿지 않는다는 것 자체가 `reach`의 결론이다).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.camera import Camera
from ..models.device import Device
from ..utils.timeutil import utcnow
from .device_view import is_stale
from .heartbeat_service import get_buffered_status
from .pi_client import PiClient
from .server_address import public_url_for

__all__ = ["run_diagnosis"]

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
_ORDER = {OK: 0, UNKNOWN: 1, WARN: 2, FAIL: 3}

# 프런트 `utils/temperature.ts`와 같은 기준: 60°C 미만 정상, 60~80 주의, 80 이상 스로틀링.
_TEMP_WARN, _TEMP_FAIL = 60.0, 80.0


def _check(key: str, label: str, status: str, detail: str, fix: str = "",
           action: Optional[str] = None) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, "fix": fix, "action": action}


def _msg(exc: HTTPException) -> str:
    d = exc.detail
    return d.get("message", str(d)) if isinstance(d, dict) else str(d)


async def _try(coro):
    """(결과, 오류). 기기 호출 실패를 예외로 흘리지 않고 점검이 판단하게 한다."""
    try:
        return await coro, None
    except HTTPException as exc:
        return None, exc


def _unreachable(key: str, label: str) -> dict:
    return _check(key, label, UNKNOWN, "기기에 닿지 않아 확인할 수 없습니다")


def _server_step_fix(step: str) -> str:
    return {
        "dns": "서버 이름을 풀지 못합니다 — 서버 주소를 IP로 두세요(PUBLIC_BASE_URL=auto 권장). .local 이름은 기기마다 해석이 다릅니다.",
        "tcp": "서버 포트에 연결되지 않습니다 — 서버가 0.0.0.0으로 떠 있는지, PC 방화벽(Windows는 인바운드 규칙, WSL은 Hyper-V 방화벽)이 막고 있지 않은지 확인하세요.",
        "http": "서버에 닿았지만 응답이 정상이 아닙니다 — 서버 프로세스가 살아 있는지 확인하세요.",
    }[step]


async def run_diagnosis(db: Session, device: Device) -> dict:
    client = PiClient(device.ip)
    expected_url = public_url_for(device.ip)

    version, version_err = await _try(client.get_version())
    reachable = version_err is None
    checks: list[dict] = []

    if reachable:
        checks.append(_check("reach", "서버 → 기기 연결", OK,
                             f"VisionGuide {version.get('version', '?')} 응답 ({device.ip})"))
    else:
        checks.append(_check(
            "reach", "서버 → 기기 연결", FAIL, f"{device.ip}에 연결하지 못했습니다: {_msg(version_err)}",
            "기기 전원·LED를 확인하고, 이 PC와 같은 Wi-Fi인지 보세요. 기기 IP가 바뀌었을 수 있으니 "
            "기기 추가의 자동 탐색으로 다시 찾으세요."))

    # 아래 호출들은 서로 독립이라 동시에 던진다.
    if reachable:
        (upd, upd_err), (ident, ident_err), (outbox, _), (diag, diag_err), (metrics, _), (status, _) = \
            await asyncio.gather(
                _try(client.get_update_status()), _try(client.get_identity()), _try(client.get_outbox()),
                _try(client.get_diagnose()), _try(client.get_metrics()), _try(client.get_status()))
    else:
        upd = upd_err = ident = ident_err = outbox = diag = diag_err = metrics = status = None

    # 펌웨어 -------------------------------------------------------------
    if not reachable:
        checks.append(_unreachable("firmware", "기기 코드 버전"))
    elif upd_err is None:
        checks.append(_check("firmware", "기기 코드 버전", OK,
                             f"업데이트 지원 (번들 {upd.get('bundle_id') or '미적용'})"))
    elif upd_err.status_code == 404:
        checks.append(_check("firmware", "기기 코드 버전", WARN,
                             "구버전 — 대시보드 푸시 업데이트와 키 없는 인수를 지원하지 않습니다",
                             "Pi에서 설치 스크립트를 실행하거나 PC에서 make sync로 한 번 올리세요."))
    else:
        checks.append(_check("firmware", "기기 코드 버전", UNKNOWN, _msg(upd_err)))

    # 신원 ---------------------------------------------------------------
    if not reachable:
        checks.append(_unreachable("identity", "기기가 아는 서버 주소"))
    elif ident_err is not None:
        checks.append(_check("identity", "기기가 아는 서버 주소", UNKNOWN, _msg(ident_err)))
    elif not ident.get("registered"):
        checks.append(_check("identity", "기기가 아는 서버 주소", FAIL, "기기에 신원이 없습니다",
                             "신원 재주입으로 이 서버를 기기에 알려 주세요.", "provision"))
    elif ident.get("device_id") != device.id:
        checks.append(_check("identity", "기기가 아는 서버 주소", FAIL,
                             f"이 주소의 기기는 {ident.get('device_id')}입니다 (등록된 id: {device.id})",
                             "같은 IP를 다른 기기가 쓰고 있습니다 — 자동 탐색으로 올바른 기기를 찾으세요."))
    else:
        known = (ident.get("server_url") or "").rstrip("/")
        if not expected_url:
            checks.append(_check("identity", "기기가 아는 서버 주소", WARN,
                                 f"기기: {known or '(없음)'} — 이 서버의 주소를 계산하지 못했습니다",
                                 "PUBLIC_BASE_URL을 확인하세요."))
        elif known != expected_url:
            checks.append(_check("identity", "기기가 아는 서버 주소", FAIL,
                                 f"기기: {known or '(없음)'} / 이 서버: {expected_url}",
                                 "서버 IP가 바뀌었습니다 — 서버 주소를 갱신하면 기기에 새 주소를 알립니다.",
                                 "refresh_address"))
        else:
            checks.append(_check("identity", "기기가 아는 서버 주소", OK, known))

    # 제어 키 ------------------------------------------------------------
    if device.control_key:
        checks.append(_check("provisioned", "서버의 기기 제어 키", OK, "이 서버가 기기를 제어할 수 있습니다"))
    else:
        checks.append(_check("provisioned", "서버의 기기 제어 키", FAIL,
                             "이 서버가 기기의 키를 모릅니다 — 재시작·코드 업데이트를 할 수 없습니다",
                             "신원 재주입을 하세요. (구버전 기기면 먼저 코드를 한 번 올려야 합니다)", "provision"))

    # 기기 → 서버 하트비트 -----------------------------------------------
    buffered = await get_buffered_status(device.id)
    last_seen = (buffered or {}).get("updated_at") or device.last_seen
    hb_err = ((outbox or {}).get("heartbeat") or {}).get("last_error")
    if last_seen is not None and not is_stale(last_seen):
        age = int((utcnow() - last_seen).total_seconds())
        checks.append(_check("link", "기기 → 서버 하트비트", OK, f"{age}초 전에 받았습니다"))
    else:
        seen = "한 번도 받지 못했습니다" if last_seen is None else f"마지막 수신 {last_seen:%Y-%m-%d %H:%M:%S} (UTC)"
        why = f" · 기기가 보고한 오류: {hb_err}" if hb_err else ""
        checks.append(_check("link", "기기 → 서버 하트비트", FAIL, seen + why,
                             "아래 '기기 → 서버 접속'과 '기기가 아는 서버 주소' 항목이 원인을 알려 줍니다."))

    # 기기에서 서버로 접속 ------------------------------------------------
    if not reachable:
        checks.append(_unreachable("server_from_device", "기기 → 서버 접속"))
    elif diag_err is not None:
        checks.append(_check("server_from_device", "기기 → 서버 접속", UNKNOWN,
                             "구버전 기기라 확인할 수 없습니다" if diag_err.status_code == 404 else _msg(diag_err),
                             "코드를 업데이트하면 확인할 수 있습니다.", "update"))
    else:
        srv = diag.get("server")
        if not srv:
            checks.append(_check("server_from_device", "기기 → 서버 접속", WARN,
                                 "기기에 서버 주소가 없어 접속을 시험하지 못했습니다", "", "provision"))
        else:
            failed = next(((k, srv[k]) for k in ("dns", "tcp", "http") if srv.get(k) and not srv[k]["ok"]), None)
            if failed:
                step, info = failed
                checks.append(_check("server_from_device", "기기 → 서버 접속", FAIL,
                                     f"{srv['url']} — {info.get('detail') or step + ' 실패'}", _server_step_fix(step)))
            else:
                checks.append(_check("server_from_device", "기기 → 서버 접속", OK,
                                     f"{srv['url']} ({(srv.get('http') or {}).get('ms', '?')}ms)"))

    # 카메라 -------------------------------------------------------------
    configured = [c for c in db.query(Camera).filter(Camera.device_id == device.id,
                                                      Camera.is_active.is_(True)).all()]
    if not reachable or metrics is None:
        checks.append(_unreachable("cameras", "카메라"))
    elif not configured:
        checks.append(_check("cameras", "카메라", WARN, "활성 카메라 프로필이 없습니다",
                             "기기 상세의 카메라 설정을 확인하세요."))
    else:
        live = {m.get("camera_id"): m for m in metrics.get("cameras", [])}
        down = [c.id for c in configured
                if not (live.get(c.id) or {}).get("streaming") or (live.get(c.id) or {}).get("stale")]
        if down:
            checks.append(_check(
                "cameras", "카메라", WARN, f"스트리밍하지 않는 카메라: {', '.join(down)}",
                "카메라가 실제로 연결돼 있는지 확인하세요. 쓰지 않는 카메라면 설정에서 비활성화하면 "
                "'주의' 표시가 사라집니다."))
        else:
            checks.append(_check("cameras", "카메라", OK, f"{len(configured)}대 스트리밍 중"))

    # 전원 · 온도 · 시계 -------------------------------------------------
    power = (diag or {}).get("power") if diag else None
    if not reachable:
        checks.append(_unreachable("power", "전원"))
    elif diag_err is not None:
        checks.append(_check("power", "전원", UNKNOWN, "구버전 기기라 확인할 수 없습니다", "", "update"))
    elif power is None:
        checks.append(_check("power", "전원", UNKNOWN, "전원 상태를 읽을 수 없습니다 (라즈베리파이가 아닐 수 있습니다)"))
    elif power["now"]["undervoltage"] or power["now"]["throttled"]:
        checks.append(_check("power", "전원", FAIL, f"지금 저전압/스로틀링 중입니다 ({power['raw']})",
                             "정격 어댑터(5V 3A 이상)와 케이블을 쓰고 있는지, PoE면 공급 전력을 확인하세요."))
    elif power["ever"]["undervoltage"] or power["ever"]["throttled"]:
        checks.append(_check("power", "전원", WARN, f"부팅 이후 저전압이 발생한 적이 있습니다 ({power['raw']})",
                             "지금은 정상이지만 전원 공급이 불안정할 수 있습니다."))
    else:
        checks.append(_check("power", "전원", OK, "부팅 이후 저전압/스로틀링 없음"))

    temp = (status or {}).get("cpu_temp_c")
    if not reachable or temp is None:
        checks.append(_unreachable("temp", "CPU 온도") if not reachable
                      else _check("temp", "CPU 온도", UNKNOWN, "온도를 읽을 수 없습니다"))
    elif temp >= _TEMP_FAIL:
        checks.append(_check("temp", "CPU 온도", FAIL, f"{temp:.1f}°C — 스로틀링 구간", "방열과 팬 동작을 확인하세요."))
    elif temp >= _TEMP_WARN:
        checks.append(_check("temp", "CPU 온도", WARN, f"{temp:.1f}°C — 높은 편입니다", "방열/통풍을 확인하세요."))
    else:
        checks.append(_check("temp", "CPU 온도", OK, f"{temp:.1f}°C"))

    ntp = (diag or {}).get("ntp_synchronized") if diag else None
    if not reachable:
        checks.append(_unreachable("clock", "시계(NTP)"))
    elif diag_err is not None or ntp is None:
        checks.append(_check("clock", "시계(NTP)", UNKNOWN, "확인할 수 없습니다"))
    elif ntp:
        checks.append(_check("clock", "시계(NTP)", OK, "동기화됨"))
    else:
        checks.append(_check("clock", "시계(NTP)", WARN, "동기화되지 않았습니다",
                             "이벤트 시각이 어긋나 통계가 엉킬 수 있습니다 — make setup-ntp"))

    overall = max((c["status"] for c in checks), key=_ORDER.__getitem__)
    return {"overall": overall, "checks": checks, "checked_at": utcnow().isoformat(timespec="seconds") + "Z"}
