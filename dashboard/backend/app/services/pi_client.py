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
| 코드 업데이트 | `POST /api/update` (multipart `file`, `X-Device-Key`) · `POST /api/update/rollback` · `GET /api/update/status` |
| 기기 판별 | `GET /api/version` → `{version, product:"VisionGuide", ...}` |
| 검증 재생 | `/api/replay/{videos,start,pause,step,stop,status,stream.mjpg}` — 세션은 기기에 하나 |
| 오탐 관리 | `/api/static-mask/{candidates,apply,hits,thumb}`·`DELETE /api/static-mask`·`/api/fp-hotspots` (`?camera=`) |
| Wi-Fi | `/api/network/{status,scan,connect,connect-result}` — `/api/network/ap`는 일부러 안 부른다 |
| **녹화·수집** | **카메라 MJPEG 포트**의 `/recording/*`·`/calibrate/*` — `PiClient(ip, camera.port)` |

이 표와 실제 Pi 라우트의 일치는 루트 `tests/test_dashboard_pi_routes.py`가 소스를 읽어 검사한다.

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
                       headers: dict[str, str] | None = None,
                       timeout: Any = None) -> httpx.Response:
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                res = await client.request(
                    method, url, params=params, json=json, files=files,
                    headers=headers,
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
                            registered_at: str = "", current_key: str = "") -> dict:
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
        headers = {"X-Device-Key": current_key} if current_key else None
        res = await self._request("POST", "/api/identity", json=payload, headers=headers)
        return res.json()

    # ------------------------------------------------------------------ 업데이트

    async def post_update(self, bundle: bytes, device_key: str, include_models: bool = False) -> dict:
        """코드 번들(tar.gz)을 기기에 올린다. 기기는 검증·적용 뒤 **응답을 먼저 주고** 재시작한다.

        `X-Device-Key`가 필요하다 — 코드를 받아 적용하는 경로다. 번들이 수십 MB(모델 포함)일
        수 있어 타임아웃을 넉넉히 잡는다. 422는 번들 검증 실패(문법·해시 등)이고 이때 기기는
        그대로다.
        """
        files = {"file": ("bundle.tar.gz", bundle, "application/gzip")}
        try:
            res = await self._request(
                "POST", "/api/update", params={"include_models": str(include_models).lower()},
                files=files, headers={"X-Device-Key": device_key},
                timeout=httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0),
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                raise HTTPException(
                    status_code=502,
                    detail="기기에 업데이트 기능이 없습니다 — 처음 한 번은 make sync로 배포하세요",
                ) from exc
            raise
        return res.json()

    async def get_update_status(self) -> dict:
        """`{bundle_id, has_backup}` — 적용된 번들. 업데이트 기능이 없는 기기는 404."""
        return await self._get_json("/api/update/status")

    async def post_rollback(self, device_key: str) -> dict:
        return await self.post_control("/api/update/rollback", device_key)

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

    async def scan_cameras(self) -> list[dict]:
        """Return cameras detected by libcamera, including legacy-mode cameras."""
        data = await self._get_json("/api/cameras/scan")
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

    async def list_audio(self) -> list[dict]:
        """기기 audio_dir의 `[{name, path, size}]`."""
        data = await self._get_json("/api/audio/list")
        return data.get("files", []) if isinstance(data, dict) else []

    # ------------------------------------------------------------------ RF 리모컨

    async def get_rf_config(self) -> dict:
        """`{config: <rf_config.json 원본>, audio_files: [Pi 절대경로...]}`."""
        data = await self._get_json("/api/rf/config")
        return data if isinstance(data, dict) else {"config": {}, "audio_files": []}

    async def put_rf_audio(self, paths: list[str]) -> dict:
        """리모컨 한 번에 순서대로 재생할 음성 목록. 다른 RF 설정 키는 Pi가 보존한다."""
        res = await self._request("PUT", "/api/rf/audio", json={"audio_files": paths})
        return res.json()

    async def put_rf_group(self, enabled: bool, priority: int) -> dict:
        """군집 제어 on/off와 우선순위(작을수록 먼저 재생)."""
        res = await self._request("PUT", "/api/rf/group",
                                  json={"group_enabled": enabled, "group_priority": priority})
        return res.json()

    async def put_rf_detection(self, rssi_threshold: int) -> dict:
        """리모컨 감지 임계값(RSSI, 1~255). 낮을수록 먼 거리에서도 반응한다."""
        res = await self._request("PUT", "/api/rf/detection",
                                  json={"rssi_threshold": rssi_threshold})
        return res.json()

    # ------------------------------------------------------------------ 통계

    async def get_timeseries(self, camera_id: Optional[str] = None,
                             period: str = "today") -> dict:
        """`{granularity: "hour"|"day", points: [...]}` — 서버 §8과 키 이름이 다르다."""
        params: dict[str, str] = {"period": period}
        if camera_id:
            params["camera"] = camera_id
        return await self._get_json("/api/stats/timeseries", params=params)

    # ------------------------------------------------------------------ 녹화·수집
    # ★ 이 절의 메서드는 roi_editor(5000)가 아니라 **카메라 MJPEG 포트**에 있다
    # (`device/camera_live_pi.py`의 `_route_recording`/`_route_calibrate`). 프레임을
    # 쥔 프로세스만 녹화할 수 있어서다. `PiClient(ip, camera.port)`로 만들어 쓴다.

    async def recording_status(self) -> dict:
        return await self._get_json("/recording/status")

    async def recording_start(self, raw: bool = False) -> dict:
        """`raw=True`면 오버레이 이전 프레임을 저장한다(학습용 촬영)."""
        res = await self._request("POST", "/recording/start",
                                  params={"raw": "1"} if raw else None)
        return res.json()

    async def recording_stop(self) -> dict:
        res = await self._request("POST", "/recording/stop")
        return res.json()

    async def recording_list(self) -> list[dict]:
        data = await self._get_json("/recording/list")
        return data.get("clips", []) if isinstance(data, dict) else []

    async def calibration_status(self) -> dict:
        return await self._get_json("/calibrate/status")

    async def calibration_start(self, seconds: float) -> dict:
        """폐장 시간 구조물 수집. Pi가 10~1800초로 자른다."""
        res = await self._request("POST", "/calibrate/start", params={"seconds": str(seconds)})
        return res.json()

    async def calibration_cancel(self) -> dict:
        res = await self._request("POST", "/calibrate/cancel")
        return res.json()

    # ------------------------------------------------------------------ 검증 재생

    async def replay_videos(self) -> list[dict]:
        data = await self._get_json("/api/replay/videos")
        return data.get("videos", []) if isinstance(data, dict) else []

    async def replay_start(self, payload: dict) -> dict:
        """`{video, conf?, model_variant?, require_person?, speed?, loop?, debug_gates?}`."""
        # 추론 백엔드를 이때 import하므로(Pi에서 수 초) 기본 5초로는 부족하다.
        res = await self._request("POST", "/api/replay/start", json=payload, timeout=30.0)
        return res.json()

    async def replay_pause(self, paused: Optional[bool] = None) -> dict:
        """`paused=None`이면 토글."""
        res = await self._request("POST", "/api/replay/pause", json={"paused": paused})
        return res.json()

    async def replay_step(self) -> dict:
        res = await self._request("POST", "/api/replay/step")
        return res.json()

    async def replay_stop(self) -> dict:
        res = await self._request("POST", "/api/replay/stop")
        return res.json()

    async def replay_status(self) -> dict:
        return await self._get_json("/api/replay/status")

    # ------------------------------------------------------------------ 오탐 관리

    @staticmethod
    def _camera_params(camera_id: Optional[str], **extra: Any) -> dict:
        params = {k: str(v) for k, v in extra.items() if v is not None}
        if camera_id:
            params["camera"] = camera_id
        return params

    async def mask_candidates(self, camera_id: Optional[str]) -> list[dict]:
        data = await self._get_json("/api/static-mask/candidates",
                                    params=self._camera_params(camera_id))
        return data.get("candidates", []) if isinstance(data, dict) else []

    async def mask_apply(self, camera_id: Optional[str], ids: list[int]) -> dict:
        """**선택한 것만 남기는 전체 치환** — 빈 목록이면 마스크를 모두 끈다."""
        res = await self._request("POST", "/api/static-mask/apply",
                                  params=self._camera_params(camera_id), json={"ids": ids})
        return res.json()

    async def mask_clear(self, camera_id: Optional[str]) -> dict:
        """후보·적용 상태·적중 기록을 모두 지운다(재수집 전)."""
        res = await self._request("DELETE", "/api/static-mask",
                                  params=self._camera_params(camera_id))
        return res.json()

    async def mask_hits(self, camera_id: Optional[str]) -> Any:
        data = await self._get_json("/api/static-mask/hits",
                                    params=self._camera_params(camera_id))
        return data.get("hits") if isinstance(data, dict) else data

    async def fp_hotspots(self, camera_id: Optional[str], min_count: int = 30,
                          limit: int = 5) -> list[dict]:
        data = await self._get_json("/api/fp-hotspots", params=self._camera_params(
            camera_id, min_count=min_count, limit=limit))
        return data.get("hotspots", []) if isinstance(data, dict) else []

    async def fp_hotspots_clear(self, camera_id: Optional[str]) -> dict:
        res = await self._request("DELETE", "/api/fp-hotspots",
                                  params=self._camera_params(camera_id))
        return res.json()

    # ------------------------------------------------------------------ 네트워크
    # `/api/network/ap`는 일부러 없다 — 원격에서 AP로 돌리면 그 순간 기기가 망에서
    # 사라져 대시보드로는 되돌릴 수 없다(현장에 가야 한다).

    async def network_status(self) -> dict:
        """`{mode: "ap"|"station"|"disconnected", ssid, ip, hostname}` (wlan0 기준)."""
        return await self._get_json("/api/network/status")

    async def network_scan(self) -> list[dict]:
        # nmcli 스캔은 Pi에서 최대 15초 걸린다(network_manager.scan_networks).
        res = await self._request("GET", "/api/network/scan", timeout=20.0)
        data = res.json()
        return data.get("networks", []) if isinstance(data, dict) else []

    async def network_connect(self, ssid: str, password: str) -> dict:
        res = await self._request("POST", "/api/network/connect",
                                  json={"ssid": ssid, "password": password})
        return res.json()

    async def network_connect_result(self) -> dict:
        return await self._get_json("/api/network/connect-result")

    # ------------------------------------------------------------------ 바이너리 중계

    async def stream_file(self, path: str, params: dict | None = None,
                          headers: dict[str, str] | None = None):
        """큰 파일(녹화 클립·썸네일)을 메모리에 다 올리지 않고 넘긴다.

        `(status, content_type, content_length, 본문 async iterator)`를 돌려준다.
        iterator가 끝나야 연결이 닫히므로 호출부는 끝까지 소비하거나 버려야 한다.
        """
        client = httpx.AsyncClient(follow_redirects=False)
        try:
            req = client.build_request("GET", f"{self.base}{path}", params=params,
                                       headers=headers, timeout=self.timeout)
            res = await client.send(req, stream=True)
        except httpx.RequestError as exc:
            await client.aclose()
            raise HTTPException(status_code=503,
                                detail=f"기기에 연결할 수 없습니다 ({self.base}): {exc}") from exc
        if res.status_code >= 400 or res.status_code in (301, 302, 303, 307, 308):
            await res.aclose()
            await client.aclose()
            code = 404 if res.status_code == 404 else 502
            raise HTTPException(status_code=code,
                                detail=f"기기가 {path}에 {res.status_code}를 반환했습니다")

        async def body():
            try:
                async for chunk in res.aiter_bytes():
                    yield chunk
            finally:
                await res.aclose()
                await client.aclose()

        return (res.status_code, res.headers.get("content-type", "application/octet-stream"),
                res.headers.get("content-length"), body())


def _pi_detail(res: httpx.Response) -> Any:
    """Pi의 오류 본문을 그대로 살린다 — 검증 오류는 **문자열 배열**로 온다."""
    try:
        body = res.json()
    except ValueError:
        return res.text or f"기기가 {res.status_code}를 반환했습니다"
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    # 카메라 MJPEG 포트(녹화·수집)는 FastAPI가 아니라 `{"ok": false, "error": "<문장>"}`을
    # 준다. 그대로 넘기면 오류 봉투가 그 문장을 **코드**로 읽어 메시지가 비므로 문장만 꺼낸다.
    if isinstance(body, dict) and isinstance(body.get("error"), str) and "message" not in body:
        return body["error"]
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
