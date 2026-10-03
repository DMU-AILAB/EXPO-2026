# PC 중앙 대시보드 실행

새 대시보드는 PC에서 실행하고, Raspberry Pi는 포트 5000의 API 에이전트로
동작한다. 카메라 MJPEG 스트림은 Pi의 포트 8080에서 제공되며 PC 백엔드가
브라우저로 프록시한다.

## 네트워크

- PC → 각 Pi: `http://<pi-ip>:5000`
- 각 Pi → PC: `http://<pc-lan-ip>:8000`
- 브라우저 → PC 프론트엔드: `http://<pc-lan-ip>:5173`

PC 방화벽에서 TCP 8000을 허용하고, Pi와 PC가 같은 LAN 또는 서로 접근 가능한
VPN에 있어야 한다. `localhost`는 Pi가 PC를 가리키는 주소로 사용할 수 없다.

### WSL2에서 실행할 때

WSL2 기본(NAT) 모드에서는 Pi가 WSL 안의 백엔드에 닿지 못한다. Windows의
`%USERPROFILE%\.wslconfig`에 다음을 넣고 `wsl --shutdown` 후 다시 연다.

```ini
[wsl2]
networkingMode=mirrored
```

관리자 PowerShell에서 백엔드 포트의 인바운드를 연다(Windows 방화벽 + Hyper-V 방화벽).

```powershell
New-NetFirewallRule -DisplayName "VisionGuide 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
New-NetFirewallHyperVRule -Name VisionGuide8000 -DisplayName "VisionGuide 8000" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8000
```

**mirrored 모드에서는 주소가 둘로 갈린다.** Pi는 PC의 LAN IP로 들어오지만,
같은 PC의 Windows 브라우저는 자기 LAN IP로 WSL에 닿지 못하고 `localhost`로만
닿는다. 그래서

- 백엔드 `.env`의 `PUBLIC_BASE_URL`(Pi가 쓰는 주소) = `http://<pc-lan-ip>:8000`
- 프론트 `.env`의 `VITE_API_BASE`(이 PC 브라우저가 쓰는 주소) = `http://localhost:8000`

`VITE_API_BASE`를 LAN IP로 두면 로그인에서 "Failed to fetch"가 난다. 이 설정이면
대시보드는 그 PC의 브라우저에서만 쓸 수 있다(다른 PC에서 쓰려면 LAN IP로 빌드).
블루투스 페어링(Web Bluetooth)도 `localhost` 또는 HTTPS에서만 동작하므로 같은 제약이다.

## 백엔드 설정

`dashboard/backend/.env.example`을 `.env`로 복사하고 PC의 LAN 주소와 운영용
시크릿을 설정한다.

```dotenv
HOST=0.0.0.0
PORT=8000
PUBLIC_BASE_URL=http://192.168.0.50:8000
CORS_ORIGINS=http://192.168.0.50:5173
JWT_SECRET_KEY=<long-random-secret>
INITIAL_ADMIN_PASSWORD=<admin-password>
```

초기 DB와 관리자를 만든다.

```powershell
cd dashboard/backend
python -m app.db.init_db
```

개발 서버는 단일 워커로 실행한다. 하트비트 메모리 버퍼와 APScheduler가 한
프로세스에 속한다.

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

## 프론트엔드 설정

`dashboard/frontend/.env.example`을 `.env`로 복사하고 같은 백엔드 주소를
설정한다.

```dotenv
VITE_API_BASE=http://192.168.0.50:8000
```

```powershell
cd dashboard/frontend
npm ci
npm run dev -- --host 0.0.0.0
```

## Pi 등록

Pi의 `visionguide-roi-editor` 서비스는 API를 제공하며, PC 대시보드가 주 화면이다.
기존 Pi 웹 화면과 Wi-Fi 온보딩을 유지해야 하는 설치에서는 systemd 기본 설정을
그대로 사용하고, 정적 화면까지 끄려면 서비스 실행 인자에 `--api-only`를 추가한다.
PC와 같은 네트워크에 연결된 Pi는 대시보드의 Pi 검색 화면에서 바로 등록할 수 있다.
등록하면 백엔드가 다음 값을 Pi에 주입한다.

- `device_id`
- Pi가 PC로 보낼 API 키
- PC 백엔드의 `PUBLIC_BASE_URL`

등록 후 Pi는 다음을 PC 백엔드로 보낸다.

- `POST /api/events/ingest`
- `PATCH /api/devices/me/heartbeat`

API 키는 등록 성공 후 한 번만 화면에 표시된다.

## 확인 순서

1. PC에서 백엔드와 프론트엔드를 실행한다.
2. Pi에서 `http://<pi-ip>:5000/api/version`을 확인한다.
3. 대시보드의 Pi 검색에서 등록한다.
5. 장치 목록에서 온라인 상태와 카메라를 확인한다.
6. 감지 이벤트가 대시보드 통계와 이벤트 화면에 나타나는지 확인한다.
7. Pi 한 대를 중지해도 다른 Pi가 계속 온라인인지 확인한다.
8. 스트림과 개별 재시작 제어가 선택한 Pi에만 적용되는지 확인한다.
