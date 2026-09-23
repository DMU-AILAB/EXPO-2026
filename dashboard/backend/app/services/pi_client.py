"""Pi의 `roi_editor`(포트 5000)를 부르는 단일 창구.

**설정의 원본은 Pi이고 서버 DB는 캐시다** (명세 §13.0). 백엔드는 저장소가 아니라
중계자이므로, Pi를 부르는 코드가 여기저기 흩어지면 그때마다 경로·필드명이 어긋난다.
실제로 그렇게 어긋나 있었다 — 구현이 `PATCH /api/cameras/{id}`와
`PUT /api/cameras/{id}/rois`를 불렀는데 **Pi에는 둘 다 없다.**

Pi 쪽 실제 계약 (apps/roi_editor/server.py):

| 하는 일 | 호출 |
|---|---|
| 카메라 조회 | `GET /api/cameras` → `{"cameras":[...15필드...]}` |
| 카메라 저장 | `POST /api/cameras` ← `{"cameras":[...]}` — **목록 전체 치환** |
| ROI 조회 | `GET /api/rois?camera=<id>` → `rois.json` 내용 그대로 |
| ROI 저장 | `POST /api/rois?camera=<id>` ← `{rois, conf, cooldown, debounce}` — **전체 치환** |
| 신원 심기 | `POST /api/identity` |
| 오디오 업로드 | `POST /api/audio/upload` (multipart, 필드명 `file`) → `{"ok":true,"path":"<절대경로>"}` |
| 기기 판별 | `GET /api/version` → `{version, product:"VisionGuide", ...}` |

주의점 셋:

1. **리다이렉트를 따라가면 안 된다.** Pi에는 Wi-Fi 온보딩용 캡티브 포털 catch-all이
   있어 매칭되지 않은 GET이 조건에 따라 302를 준다(`server.py:773-781`).
   따라가면 "응답이 왔으니 VisionGuide 기기"로 오판한다.
2. **Pi의 400 `detail`은 문자열 배열이다** (`validate_camera_config()` 결과). 그대로
   올려보내야 현장에서 무엇이 틀렸는지 보인다.
3. **쓰기는 전체 치환이다.** 읽어서 병합한 뒤 보내지 않으면 백엔드가 모르는 필드가
   조용히 코드 기본값으로 되돌아간다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

__all__ = ["PiClient", "PiUnreachable"]

# roi_editor 포트는 고정이다 — `validate_camera_config()`가 카메라 프로필에
# 5000을 쓰지 못하도록 예약해 둔 값이기도 하다 (device/camera_config.py:20).
ROI_EDITOR_PORT = 5000

_DEFAULT_TIMEOUT = 5.0
# 스캔처럼 "없는 주소"를 대량으로 두드릴 때는 빨리 포기해야 한다.
_PROBE_TIMEOUT = httpx.Timeout(connect=0.5, read=2.0, write=1.0, pool=1.0)


class PiUnreachable(Exception):
    """기기에 닿지 못했다 — 호출부가 캐시 반환 등으로 완화할 수 있게 구분한다."""


class PiClient:
    """기기 한 대를 상대하는 클라이언트. 요청마다 새로 만들어 써도 된다."""

    def __init__(self, ip: str, port: int = ROI_EDITOR_PORT, timeout: float = _DEFAULT_TIMEOUT):
        self.base = f"http://{ip}:{port}"
        self.timeout = timeout

    # ------------------------------------------------------------------ 저수준

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: Any = None, files: Any = None,
                       timeout: Any = None) -> httpx.Response:
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                res = await client.request(
                    method, url, params=params, json=json, files=files,
                    timeout=timeout if timeout is not None else self.timeout,
                )
        except httpx.ConnectError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"기기에 연결할 수 없습니다 ({self.base}): {exc}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=504,
                detail=f"기기가 응답하지 않습니다 ({self.base}): {exc}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"기기와 통신하지 못했습니다 ({self.base}): {exc}",
            ) from exc

        if res.status_code in (301, 302, 303, 307, 308):
            # 캡티브 포털 catch-all. 요청한 엔드포인트가 이 기기에 없다는 뜻이다.
            raise HTTPException(
                status_code=502,
                detail=f"기기가 캡티브 포털로 리다이렉트했습니다 — {path}가 없는 버전일 수 있습니다",
            )

        if res.status_code >= 400:
            raise HTTPException(status_code=res.status_code, detail=_pi_detail(res))

        return res

    async def _get_json(self, path: str, params: dict | None = None) -> Any:
        res = await self._request("GET", path, params=params)
        return res.json()

    # ------------------------------------------------------------------ 판별

    async def get_version(self) -> dict:
        """`{version, product, registered, device_id}` — 기기 판별의 1차 수단."""
        return await self._get_json("/api/version")

    async def get_status(self) -> dict:
        """`{uptime_seconds, cpu_temp_c, load_avg, mem_used_mb, mem_total_mb}`.

        **이 응답 스키마를 늘리지 말 것** — 백엔드의 기기 탐색이 바디 스키마로
        VisionGuide 여부를 판별한다(`apps/roi_editor/server.py:140-143`).
        """
        return await self._get_json("/api/device/status")

    # ------------------------------------------------------------------ 신원

    async def post_identity(self, *, device_id: str, api_key: str, server_url: str,
                            name: str = "", location: str = "",
                            registered_at: str = "") -> dict:
        """서버가 발급한 신원을 기기에 심는다.

        **`server_url`이 비면 Pi는 아무것도 전송하지 않는다** — `is_usable()`이
        device_id·api_key·server_url 셋을 모두 요구하기 때문에(
        `device/device_identity.py:49-51`), 주소를 빠뜨리면 이벤트가 outbox에
        조용히 쌓이기만 한다.
        """
        if not server_url:
            raise HTTPException(
                status_code=500,
                detail="server_url이 비어 있어 기기가 서버로 전송할 수 없습니다 "
                       "(PUBLIC_BASE_URL 설정을 확인하세요)",
            )
        payload = {
            "device_id": device_id, "api_key": api_key, "server_url": server_url,
            "name": name, "location": location, "registered_at": registered_at,
        }
        res = await self._request("POST", "/api/identity", json=payload)
        return res.json()

    # ------------------------------------------------------------------ 제어

    async def post_control(self, path: str, device_key: str,
                           params: dict | None = None) -> dict:
        """재시작·재부팅처럼 기기 자체를 건드리는 호출.

        포트 5000의 나머지 라우트와 달리 `X-Device-Key`를 요구한다 — 설정 변경과
        달리 서비스를 끊는 동작이라, 같은 망에 있다는 것만으로 허용할 수 없다.
        """
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                res = await client.post(url, params=params,
                                        headers={"X-Device-Key": device_key},
                                        timeout=self.timeout)
        except httpx.ConnectError as exc:
            raise HTTPException(status_code=503,
                                detail=f"기기에 연결할 수 없습니다 ({self.base}): {exc}") from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504,
                                detail=f"기기가 응답하지 않습니다 ({self.base}): {exc}") from exc
        except httpx.RequestError as exc:
            # 재부팅은 명령을 받자마자 연결이 끊길 수 있다. 다만 **끊김을 성공으로
            # 간주하지는 않는다** — 그렇게 하면 기기가 꺼져 있을 때도 성공으로 보인다.
            raise HTTPException(status_code=502,
                                detail=f"기기와 통신하지 못했습니다 ({self.base}): {exc}") from exc

        if res.status_code == 404:
            raise HTTPException(
                status_code=502,
                detail=f"기기에 {path}가 없습니다 — roi_editor를 최신 버전으로 배포하세요",
            )
        if res.status_code >= 400:
            raise HTTPException(status_code=res.status_code, detail=_pi_detail(res))
        try:
            return res.json()
        except ValueError:
            return {"ok": True}

    # ------------------------------------------------------------------ 카메라

    async def get_cameras(self) -> list[dict]:
        data = await self._get_json("/api/cameras")
        return data.get("cameras", []) if isinstance(data, dict) else []

    async def put_cameras(self, cameras: list[dict]) -> dict:
        """**목록 전체 치환.** 부분 갱신 엔드포인트는 Pi에 없다."""
        res = await self._request("POST", "/api/cameras", json={"cameras": cameras})
        return res.json()

    async def get_model_variants(self) -> dict:
        """열거값의 단일 출처는 Pi다 — 백엔드가 하드코딩하면 모델 추가 시 어긋난다."""
        return await self._get_json("/api/model-variants")

    async def get_capture_presets(self) -> dict:
        return await self._get_json("/api/capture-presets")

    # ------------------------------------------------------------------ ROI

    async def get_rois(self, camera_id: Optional[str] = None) -> dict:
        """`rois.json` 내용 그대로 — `{rois, conf?, cooldown?, debounce?}`."""
        params = {"camera": camera_id} if camera_id else None
        data = await self._get_json("/api/rois", params=params)
        return data if isinstance(data, dict) else {"rois": []}

    async def put_rois(self, camera_id: Optional[str], payload: dict) -> dict:
        """**전체 치환.** `conf`·`cooldown`·`debounce`를 함께 보내지 않으면 기존 값이 사라진다."""
        params = {"camera": camera_id} if camera_id else None
        res = await self._request("POST", "/api/rois", params=params, json=payload)
        return res.json()

    # ------------------------------------------------------------------ 오디오

    async def upload_audio(self, file_path: Path, filename: Optional[str] = None) -> str:
        """서버가 보관한 mp3를 기기로 밀고 **Pi 로컬 절대경로**를 돌려받는다.

        ROI의 `audio_file`은 파일명이 아니라 이 절대경로여야 한다 —
        `camera_live_pi.py`가 그 경로로 그대로 재생한다.
        """
        name = filename or file_path.name
        with open(file_path, "rb") as fh:
            files = {"file": (name, fh, "audio/mpeg")}
            res = await self._request("POST", "/api/audio/upload", files=files)
        data = res.json()
        path = data.get("path")
        if not path:
            raise HTTPException(status_code=502, detail=f"기기가 오디오 경로를 돌려주지 않았습니다: {data}")
        return path

    # ------------------------------------------------------------------ 통계

    async def get_timeseries(self, camera_id: Optional[str] = None,
                             period: str = "today") -> dict:
        """`{granularity: "hour"|"day", points: [...]}` — 서버 §8과 키 이름이 다르다."""
        params: dict[str, str] = {"period": period}
        if camera_id:
            params["camera"] = camera_id
        return await self._get_json("/api/stats/timeseries", params=params)


def _pi_detail(res: httpx.Response) -> Any:
    """Pi의 오류 본문을 그대로 살린다 — 검증 오류는 **문자열 배열**로 온다."""
    try:
        body = res.json()
    except ValueError:
        return res.text or f"기기가 {res.status_code}를 반환했습니다"
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    return body


async def probe(ip: str, port: int = ROI_EDITOR_PORT) -> Optional[dict]:
    """VisionGuide 기기인지 확인하고 식별 정보를 돌려준다. 아니면 None.

    **"404가 아니면 있음" 식으로 판별하면 안 된다** — 캡티브 포털 catch-all 때문이다
    (명세 §12). `GET /api/version`의 200 + `product=="VisionGuide"`로만 판정하고,
    구형 펌웨어를 위해 `GET /api/device/status`의 5키 스키마를 보조로 쓴다.
    """
    base = f"http://{ip}:{port}"
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=_PROBE_TIMEOUT) as client:
            try:
                res = await client.get(f"{base}/api/version")
                if res.status_code == 200:
                    body = res.json()
                    if isinstance(body, dict) and body.get("product") == "VisionGuide":
                        return {
                            "version": body.get("version"),
                            "product": body.get("product"),
                            "registered": bool(body.get("registered")),
                            "device_id": body.get("device_id"),
                        }
            except (httpx.RequestError, ValueError):
                pass

            # 폴백: /api/version이 없는 구버전 — status의 바디 스키마로 판별한다.
            try:
                res = await client.get(f"{base}/api/device/status")
                if res.status_code == 200:
                    body = res.json()
                    if _looks_like_device_status(body):
                        return {"version": None, "product": "VisionGuide",
                                "registered": False, "device_id": None}
            except (httpx.RequestError, ValueError):
                pass
    except httpx.RequestError:
        return None
    return None


_STATUS_KEYS = {"uptime_seconds", "cpu_temp_c", "load_avg", "mem_used_mb", "mem_total_mb"}


def _looks_like_device_status(body: Any) -> bool:
    return isinstance(body, dict) and _STATUS_KEYS.issubset(body.keys())
