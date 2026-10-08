# PC 중앙 대시보드 실행

새 대시보드는 PC에서 실행하고, Raspberry Pi는 포트 5000의 API 에이전트로
동작한다. 카메라 MJPEG 스트림은 Pi의 포트 8080에서 제공되며 PC 백엔드가
브라우저로 프록시한다.

## 처음 시작하기 (서버 한 줄 → Pi 한 줄)

### 1. 서버 PC — Windows 네이티브

저장소를 받은 폴더에서 **`setup-server.bat`을 더블클릭**한다(또는 터미널에서 실행).
권한 확인 창이 한 번 뜬다(방화벽·자동 시작 등록에 관리자 권한이 필요하다).

```powershell
.\setup-server.bat                 # 기본: 포트 8000
.\setup-server.bat -Port 8080      # 포트 변경
.\setup-server.bat -DryRun         # 아무것도 바꾸지 않고 할 일만 보기
.\setup-server.bat -Uninstall      # 자동 시작 작업과 방화벽 규칙 제거 (파일·DB는 그대로)
```

스크립트가 하는 일: Git·Python 3.11·Node.js를 `winget`으로 설치 → `dashboard\backend\.venv` 와
의존성 → `.env`(JWT 키·관리자 비밀번호 자동 생성)와 DB → 프런트 빌드 → 방화벽(TCP 8000,
UDP 48555) → **로그온 시 자동 시작**(작업 스케줄러 `VisionGuide Dashboard`) → 시작.
끝나면 접속 주소(`http://<이 PC의 LAN 주소>:8000`)와 관리자 비밀번호를 한 번 보여 준다
(`dashboard\backend\data\admin-password.txt`에도 저장된다).

- **다시 실행해도 안전하다.** 기존 `.env`의 값(JWT 키·포트)과 DB는 바꾸지 않고 빠진 키만 채운다.
  `-ResetEnv`만 `.env`를 새로 만든다(로그인이 모두 풀린다).
- 프런트는 백엔드가 **같은 포트로 서빙**한다 — Node/Vite/CORS 설정이 필요 없다.
  코드를 받은 뒤 화면을 다시 빌드하려면 `-Rebuild`.
- **서버는 WSL이 아니라 Windows 네이티브로 돌린다.** WSL(NAT/미러)에서는 Pi가 서버에 닿지
  못하거나 Hyper-V 방화벽이 막는다(아래 "개발용: WSL2"). 코드 편집·테스트는 WSL에서 해도 된다.
- **이 PC가 절전·최대 절전에 들어가면 서버가 멈추고 기기 연결이 끊긴다.** 전원 옵션에서 끈다.
- Linux/macOS는 보조로 `bash deploy/setup-server.sh`(venv·의존성·`.env`·DB·빌드·실행 안내까지)를 쓴다.
  방화벽·자동 시작은 직접 설정한다.
- 저장소가 공개일 때는 원격 한 줄도 가능하다(관리자 PowerShell):
  `& ([scriptblock]::Create((irm https://raw.githubusercontent.com/DMU-AILAB/EXPO-2026/main/deploy/setup-server.ps1)))`
  비공개이면 `git clone` 후 `setup-server.bat`.

### 2. 새 Pi — 설치 명령 한 줄

대시보드 로그인 → **기기 추가 → 새 기기 설치**가 토큰이 든 명령을 만들어 준다. Pi에서 그 명령을
붙여 넣으면 코드·서비스·**모델**(현행 `v15_320` + 예비 `v10_320`, 약 6MB)·**핫스팟 프로필**이
설치되고 서버에 등록된다.

```bash
bash <(curl -fsSL "http://<서버>:8000/api/bootstrap/install.sh?token=<토큰>")
bash <(curl -fsSL "...") --dry-run                 # 먼저 할 일만 보기
bash <(curl -fsSL "...") --models all              # 모델 전부(수십 MB). 기본은 default, none은 코드만
bash <(curl -fsSL "...") --ap-password '내비밀번호'  # 핫스팟 비밀번호(8자 이상, 기본 visionguide)
```

- **토큰은 선택이다.** 서버가 `AUTO_ENROLL=true`(기본)이고 Pi가 사설 LAN이면 토큰 없이도 받고 등록된다.
  `--server`도 생략하면 LAN에서 서버를 스스로 찾는다(UDP 48555 — 서버 방화벽에서 열려 있어야 한다). 찾은 서버는
  **터미널에서 확인을 묻는다**(root로 설치할 코드를 내려주는 서버라서 — `--yes`로 생략).
- 모델은 이미 있으면 다시 받지 않는다. 핫스팟 프로필(`VisionGuide-AP`, `192.168.4.1`)은
  **없을 때만** 만든다 — 기존 기기의 프로필은 건드리지 않는다.
- 핫스팟 프로필이 있어야 Wi-Fi가 없을 때 핫스팟으로 전환된다(`auto_ap.sh`, Wi-Fi 버튼).

## 네트워크

- PC → 각 Pi: `http://<pi-ip>:5000`
- 각 Pi → PC: `http://<pc-lan-ip>:8000`
- 브라우저 → 대시보드: `http://<pc-lan-ip>:8000` (백엔드가 화면까지 서빙. 개발 서버를 쓰면 `:5173`)

PC 방화벽에서 TCP 8000(과 기기 자동 발견용 UDP 48555)을 허용하고(`setup-server`가 해 준다), Pi와 PC가 같은 LAN 또는 서로 접근 가능한
VPN에 있어야 한다. `localhost`는 Pi가 PC를 가리키는 주소로 사용할 수 없다.

### 개발용: WSL2에서 실행할 때

> 운영(기기가 붙는 서버)에는 쓰지 말고 위의 Windows 네이티브 설치를 쓴다. 아래는 코드를 고치며
> 개발 서버를 띄울 때의 주의사항이다.

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

## 수동 설치 · 개발 서버 설정

`setup-server`를 쓰지 않고 직접 하거나 개발 서버(`vite dev` + `uvicorn --reload`)를 띄울 때의 절차다.

### 백엔드 설정

`dashboard/backend/.env.example`을 `.env`로 복사하고 PC의 LAN 주소와 운영용
시크릿을 설정한다.

```dotenv
HOST=0.0.0.0
PORT=8000
PUBLIC_BASE_URL=auto            # 기기에 닿는 경로의 서버 IP를 그때그때 계산한다(권장). 고정하려면 URL을 쓴다
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

### 프론트엔드 설정 (개발 서버)

`npm run build`로 만든 화면은 백엔드가 같은 포트로 서빙하므로 설정이 필요 없다. 개발 서버는
`dashboard/frontend/.env.example`을 `.env`로 복사하고 백엔드 주소를 설정한다.

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

## Pi의 IP가 바뀌었을 때 (새 Wi-Fi, DHCP 임대 갱신)

서버는 DB의 `Device.ip`로 Pi를 호출한다. Pi가 새 IP를 받으면 하트비트가 온 **출발지 주소**로
이 값을 자동으로 갱신한다(`app/services/device_address.py`). 새 Wi-Fi로 옮기고 Pi가 하트비트를
보내기 시작하면 별도 조작 없이 스트림과 설정 호출이 이어진다.

- 하트비트 간격(약 15초) 안에 따라간다. 그 사이에는 대시보드에서 오프라인으로 보일 수 있다.
- 사설 대역(10/8, 172.16/12, 192.168/16)과 링크 로컬(169.254/16)의 IPv4만 받는다.
  서버가 이 주소로 제어 키(`X-Device-Key`)를 보내기 때문이다.
- 다른 기기가 이미 쓰는 IP면 바꾸지 않고 서버 로그에 경고를 남긴다(낡은 항목이 주소를 쥐고 있는 경우).
- `X-Forwarded-For`는 믿지 않는다. 서버 앞에 리버스 프록시를 두면 프록시 주소가 보여서 동작하지 않는다.

이 기능은 **Pi → 서버** 방향이 살아 있어야 한다. 새 PC로 옮겼다면 Pi의 `server_url`
(`device_identity.json`)이 여전히 이전 PC를 가리키므로, 새 PC에서 Pi를 다시 등록해야
하트비트가 도착한다(대시보드의 Pi 검색에서 **인수**).

## 확인 순서

1. PC에서 백엔드와 프론트엔드를 실행한다.
2. Pi에서 `http://<pi-ip>:5000/api/version`을 확인한다.
3. 대시보드의 Pi 검색에서 등록한다.
5. 장치 목록에서 온라인 상태와 카메라를 확인한다.
6. 감지 이벤트가 대시보드 통계와 이벤트 화면에 나타나는지 확인한다.
7. Pi 한 대를 중지해도 다른 Pi가 계속 온라인인지 확인한다.
8. 스트림과 개별 재시작 제어가 선택한 Pi에만 적용되는지 확인한다.

## Pi가 서버를 스스로 찾아 등록 (자동 발견 · 승인)

Pi의 `roi_editor`가 `JoinAgent`(`device/server_join.py`)를 함께 띄운다. 이 에이전트가 LAN에서 서버를 찾아
(UDP 브로드캐스트) 등록을 요청한다. **서버가 그 Pi의 `:5000`을 직접 읽어** 판정한다.

| 상황 | 결과 |
|---|---|
| 신원이 없는 Pi (새 기기) | 켜기만 하면 **자동 등록** |
| 이 서버 소속인데 서버 IP가 바뀜 | 승인 없이 **주소만 갱신**(키 불변) |
| **다른 서버에 등록된 Pi** (서버 PC 교체 등) | 기기 목록 상단 **"승인 대기 기기"** 에 올라옴 → 승인하면 이 서버로 이동 |
| 구버전 코드의 Pi (에이전트 없음) | 요청하지 않음 → Pi 검색의 **인수**, 또는 `install.sh`로 코드를 올린 뒤 승인 |

- 승인 대기는 기기가 5분마다 다시 알리고, **15분 동안 요청이 없으면 사라진다.** 거절하면 1시간 동안 무시한다.
- 승인하면 이전 서버와의 연결이 끊긴다. 기기에는 `[WARN] 기기 신원 인수` 로그와 부저 1회가 남는다.
- **Pi 스스로는 신원을 바꾸지 않는다.** 바뀌는 때는 서버가 심을 때(자동 등록·승인·주소 갱신)뿐이다.
- 끄려면 서버 `.env`의 `AUTO_ENROLL=false`. 방화벽 UDP 48555는 `setup-server`가 연다.
- 브로드캐스트가 막힌 망(AP 격리·VLAN)에서는 발견되지 않는다 — 그때는 대시보드 "기기 추가"의 수동 등록·설치 토큰을 쓴다.
