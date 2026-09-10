# Wi-Fi 온보딩 및 IP 탐색 가이드

> VisionGuide Pi를 새 장소에 설치할 때 — 네트워크 설정부터 브라우저 접속까지 전체 흐름

---

## 개요

Pi가 처음 켜지거나 저장된 Wi-Fi가 없는 환경에서는 자동으로 **AP(핫스팟) 모드**로 전환됩니다.
사용자는 스마트폰 또는 PC로 Pi의 핫스팟에 접속한 뒤, 브라우저 팝업(캡티브 포털)을 통해
대시보드에서 홈 Wi-Fi를 검색·연결합니다. 이후에는 `discover.py` 또는 mDNS 주소로 바로 접근할 수 있습니다.

```
[부팅]
  │
  ├─ 저장된 Wi-Fi 연결 성공 ──► Station 모드 (정상 운영)
  │
  └─ 연결 실패 / 저장된 Wi-Fi 없음
       │
       ▼
  AP 모드 자동 전환 (VisionGuide-AP)
       │
       ▼
  폰/PC가 VisionGuide-AP 접속 → 브라우저 팝업 자동 등장
       │
       ▼
  대시보드에서 Wi-Fi 검색 → 비밀번호 입력 → 연결
       │
       ▼
  Station 모드 전환 (홈 Wi-Fi 연결됨)
       │
       ▼
  PC에서 discover.py 실행 → IP 조회 → 브라우저 접속
```

---

## 구성 요소

| 파일 | 역할 |
|------|------|
| `roi_editor/network_manager.py` | nmcli 래퍼 — 네트워크 상태 조회, Wi-Fi 스캔, 연결, AP 전환 |
| `roi_editor/server.py` | 네트워크 API 5개 + 캡티브 포털 엔드포인트 |
| `roi_editor/static/index.html` | AP 배너 + 네트워크 카드 + Wi-Fi 설정 패널 UI |
| `deploy/auto_ap.sh` | 부팅 시 네트워크 미연결이면 AP 자동 전환 + iptables 설정 |
| `deploy/visionguide-auto-ap.service` | `auto_ap.sh`를 실행하는 oneshot systemd 유닛 |
| `deploy/visionguide-network.sudoers` | ailab 유저가 nmcli/iptables를 NOPASSWD로 실행하는 sudoers 규칙 |
| `deploy/visionguide-avahi.service` | Avahi mDNS 광고 — `raspberrypi.local:5000` 자동 노출 |
| `discover.py` | PC에서 실행 — 서브넷 전체를 스캔해 VisionGuide Pi URL 탐색 |

---

## 1. 부팅 시 자동 AP 전환

`visionguide-auto-ap.service`(Type=oneshot)가 NetworkManager 이후 실행됩니다.

```
[NetworkManager 시작]
      ↓ (약 15초 대기)
[auto_ap.sh 실행]
      ↓
wlan0 연결 상태 확인
  ├─ 연결됨(station) → 아무것도 하지 않음
  ├─ 이미 VisionGuide-AP → iptables + dnsmasq 재적용
  └─ 연결 없음 → nmcli connection up VisionGuide-AP
                  → iptables HTTP(80→5000) 리다이렉트 설정
                  → dnsmasq에 captive conf 작성 (모든 DNS → 192.168.4.1)
```

**AP 모드 고정 정보:**

| 항목 | 값 |
|------|----|
| SSID | `VisionGuide-AP` |
| Pi IP | `192.168.4.1` |
| 대시보드 URL | `http://192.168.4.1:5000` |

---

## 2. 캡티브 포털 (자동 팝업)

AP에 접속한 기기가 인터넷 연결을 확인하는 순간 Pi가 팝업을 유도합니다.

**동작 원리:**

```
기기가 VisionGuide-AP 접속
  │
  ├─ [DNS] 모든 쿼리 → 192.168.4.1 (dnsmasq address=/#/...)
  │
  ├─ [HTTP 80] iptables PREROUTING → 포트 5000으로 리다이렉트
  │
  └─ OS별 감지 URL 요청:
       iOS:     GET http://captive.apple.com/hotspot-detect.html
       Android: GET http://connectivitycheck.gstatic.com/generate_204
       Windows: GET http://www.msftconnecttest.com/connecttest.txt
         │
         ▼
       FastAPI → 302 → http://192.168.4.1:5000
         │
         ▼
       OS가 "인터넷 로그인 필요" 팝업 표시
```

> **iOS 참고:** iOS 14 이후 HTTPS 캡티브 포털 감지도 병행하지만, HTTP 리다이렉트만으로도 팝업이 뜹니다. HTTPS는 유효한 인증서 없이는 자동 팝업 불가능하며, "수동으로 열기" 알림으로 표시됩니다.

---

## 3. 대시보드 Wi-Fi 설정 UI

팝업이 열리면(또는 수동으로 `http://192.168.4.1:5000` 접속 시) 대시보드가 표시됩니다.

**화면 구성:**

```
┌─────────────────────────────────────────────────────┐
│ [amber 배너] 현재 VisionGuide-AP 모드입니다.          │
│             Wi-Fi를 설정해 홈 네트워크에 연결하세요.   │
│                                    [Wi-Fi 설정 →]    │
├─────────────────────────────────────────────────────┤
│ 모니터링 탭                                           │
│                                                     │
│  [네트워크 카드]                                      │
│   네트워크           [AP 모드 ●]                      │
│   IP: 192.168.4.1                                   │
│   [Wi-Fi 설정] ← 클릭 시 아래 패널 펼쳐짐             │
│   ┌─────────────────────────────────────────────┐   │
│   │ 주변 네트워크              [스캔]             │   │
│   │ ████████████ HomeNetwork  88%  WPA2         │   │
│   │ ████████░░░░ Guest        54%  개방          │   │
│   │                                             │   │
│   │ SSID     [HomeNetwork___________]           │   │
│   │ 비밀번호  [**********************]           │   │
│   │                      [연결]                 │   │
│   │                                             │   │
│   │ 연결 시도 중… (3초 후 AP가 종료됩니다)        │   │
│   │ 홈 Wi-Fi 재접속 후 아래 주소로 이동하세요:    │   │
│   │ http://visionguide.local:5000               │   │
│   └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

**연결 절차:**

1. **스캔** 버튼 클릭 → 주변 Wi-Fi 목록 (신호 강도 내림차순)
2. 목록에서 SSID 클릭 → 자동 입력 (또는 직접 입력)
3. 비밀번호 입력 후 **연결** 클릭
4. 서버가 즉시 200 응답 반환 (AP 아직 살아있음)
5. **3초 후** 백그라운드 스레드가 `nmcli connection up <SSID>` 실행 → AP 종료
6. 폰/PC를 홈 Wi-Fi로 전환 후 아래 주소로 접속:

```
http://raspberrypi.local:5000    ← mDNS (추천)
http://<새IP>:5000               ← discover.py로 조회
```

**3초 딜레이 설계 이유:**

```
[POST /api/network/connect 수신]
         │
         ▼ 즉시 반환 (200 OK)
[브라우저 응답 수신 완료 ~100ms]
         │
    ─── 3초 대기 ───
         │
[nmcli connection up <SSID>]   ← AP 종료
         │
    DHCP IP 할당 대기 2초
         │
[GET /api/network/connect-result] ← 폴링으로 새 IP 확인
```

---

## 4. 네트워크 API

`roi_editor/server.py`에 추가된 5개 엔드포인트:

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/api/network/status` | 현재 모드·SSID·IP·hostname |
| `GET` | `/api/network/scan` | 주변 Wi-Fi 목록 (신호 강도 순) |
| `POST` | `/api/network/connect` | `{ssid, password}` → 백그라운드 연결 시작 |
| `GET` | `/api/network/connect-result` | 연결 결과 폴링 (2초 간격 권장) |
| `POST` | `/api/network/ap` | AP 모드로 전환 |

**`GET /api/network/status` 응답 예시:**

```json
// Station 모드
{"mode": "station", "ssid": "204_WIFI", "ip": "192.168.0.89", "hostname": "raspberrypi"}

// AP 모드
{"mode": "ap", "ssid": "VisionGuide-AP", "ip": "192.168.4.1", "hostname": "raspberrypi"}

// 미연결
{"mode": "disconnected", "ssid": null, "ip": null, "hostname": "raspberrypi"}
```

**`GET /api/network/scan` 응답 예시:**

```json
{
  "networks": [
    {"ssid": "HomeNetwork", "signal_pct": 88, "security": "WPA2"},
    {"ssid": "Guest",       "signal_pct": 54, "security": ""},
    {"ssid": "Neighbor",    "signal_pct": 31, "security": "WPA2"}
  ]
}
```

**`POST /api/network/connect` 요청/응답:**

```json
// 요청
{"ssid": "HomeNetwork", "password": "mypassword"}

// 응답 (즉시, AP 아직 살아있음)
{"ok": true, "message": "연결 시도 중. 3초 후 'HomeNetwork' 네트워크로 전환됩니다.", "delay_seconds": 3}
```

**`GET /api/network/connect-result` 응답:**

```json
// 연결 중
{"in_progress": true, "result": null}

// 성공
{"in_progress": false, "result": {"status": "ok", "ip": "192.168.0.89"}}

// 실패
{"in_progress": false, "result": {"status": "error", "error": "인증 실패"}}
```

---

## 5. PC에서 Pi IP 조회 (discover.py)

서브넷 전체를 병렬 스캔해서 포트 5000에 응답하는 VisionGuide Pi를 찾습니다.
표준 라이브러리만 사용 — 별도 패키지 설치 불필요.

**기본 사용:**

```bash
python discover.py
```

```
🔍  VisionGuide Pi 탐색 중 — 192.168.0.1-254 포트 5000
    (mDNS가 동작하면 http://visionguide.local:5000 도 시도해 보세요)

  [██████████████████████████████] 254/254

  ✅  1개 발견:

    http://192.168.0.89:5000  (raspberrypi | 204_WIFI | station)
```

**옵션:**

| 옵션 | 설명 |
|------|------|
| `python discover.py` | 탐색 후 URL 출력 (약 3~4초) |
| `python discover.py --open` | 탐색 후 첫 번째 Pi를 브라우저로 자동 실행 |
| `python discover.py --subnet 10.0.1` | 서브넷 직접 지정 (자동 감지 실패 시) |

**동작 방식:**

1. PC의 기본 게이트웨이 방향 IP에서 서브넷 자동 감지 (예: `192.168.0.x`)
2. `.1` ~ `.254` 총 254개 IP에 포트 5000 TCP 연결 시도 (80스레드 병렬, timeout 0.4초)
3. 포트 열린 IP에 `GET /api/network/status` 요청으로 VisionGuide 여부 확인
4. 결과 출력 (hostname · SSID · mode 포함)

> **팁:** Pi가 같은 서브넷에 없거나 방화벽이 막혀있으면 탐색되지 않습니다.

---

## 6. mDNS 접속 (raspberrypi.local)

Avahi가 Pi의 HTTP 서비스를 LAN에 광고합니다.
IP 조회 없이 고정 주소로 접속 가능합니다.

```
http://raspberrypi.local:5000
```

**OS별 지원 현황:**

| OS | 지원 | 조건 |
|----|------|------|
| macOS | ✅ | 기본 지원 (Bonjour 내장) |
| iOS | ✅ | 기본 지원 |
| Android | ✅ | Android 12+ 기본, 구버전은 앱 필요 |
| Windows 10/11 | ✅ | 빌드 1703+ 기본 지원 |
| Linux | ✅ | `avahi-daemon` 설치 필요 |

> Pi의 hostname이 `raspberrypi`이면 `raspberrypi.local`로 접근합니다.
> hostname을 `visionguide`로 변경하면 `visionguide.local`이 됩니다.
> (`sudo hostnamectl set-hostname visionguide` 후 재부팅)

---

## 7. 설치 및 배포

### 최초 설치 (새 Pi)

```bash
# 전체 배포 (파일 전송 + 의존성 설치)
make deploy

# systemd 서비스 등록 (최초 1회)
make install-service
```

`make install-service`가 설치하는 서비스:

| 서비스 | 역할 |
|--------|------|
| `visionguide-device` | camera_live_pi.py — 탐지·음성 안내 |
| `visionguide-roi-editor` | roi_editor/server.py — 대시보드 포트 5000 |
| `visionguide-controls` | gpio_controls.py — Wi-Fi 전환 버튼 |
| `visionguide-fan` | fan_controller.py — 냉각팬 제어 |
| `visionguide-auto-ap` | auto_ap.sh — 부팅 시 자동 AP 전환 (신규) |

`/etc/sudoers.d/visionguide-network`에 nmcli·iptables NOPASSWD 규칙도 함께 설치됩니다.

### 코드 변경 후 빠른 업데이트

```bash
# ROI 에디터 관련 파일만 재전송
make sync-roi-editor
```

### 수동 AP 전환 (테스트용)

```bash
# Pi에서 직접 실행
sudo nmcli connection up VisionGuide-AP
sudo systemctl start visionguide-auto-ap  # iptables + dnsmasq도 재적용
```

---

## 8. 트러블슈팅

### 캡티브 포털 팝업이 안 뜰 때

| 증상 | 원인 | 해결 |
|------|------|------|
| 팝업 없음, 브라우저도 안 열림 | dnsmasq conf 미적용 | `sudo systemctl restart visionguide-auto-ap` |
| DNS는 되는데 HTTP 리다이렉트 안 됨 | iptables 규칙 없음 | `sudo iptables -t nat -L PREROUTING` 확인 후 `auto_ap.sh` 재실행 |
| iOS만 팝업 안 뜸 | HTTPS 감지 실패 | 수동으로 `http://192.168.4.1:5000` 접속 |
| 팝업 뜨는데 대시보드가 안 열림 | roi-editor 서비스 다운 | `sudo systemctl status visionguide-roi-editor` |

### Wi-Fi 연결 후 새 IP를 모를 때

```bash
# 방법 1: discover.py (PC에서)
python discover.py

# 방법 2: mDNS
http://raspberrypi.local:5000

# 방법 3: Pi에 SSH 접속 후 직접 확인
ssh ailab@raspberrypi.local
nmcli device show wlan0 | grep IP4.ADDRESS
```

### 연결 실패 (잘못된 비밀번호 등)

`GET /api/network/connect-result`를 폴링하면 약 30초 후 오류 메시지가 반환됩니다.
오류 확인 후 비밀번호를 다시 입력해 재시도하세요.

### AP 모드로 돌아가고 싶을 때

대시보드 네트워크 카드의 **Wi-Fi 설정** → API 호출로 전환하거나,
물리 버튼(GPIO17)을 누르거나, 직접 SSH:

```bash
ssh ailab@<현재IP>
sudo nmcli connection up VisionGuide-AP
```

---

## 9. 관련 파일 맵

```
EXPO-2026/
├── discover.py                        ← PC에서 Pi IP 탐색
├── roi_editor/
│   ├── network_manager.py             ← nmcli 래퍼 (신규)
│   ├── server.py                      ← 네트워크 API + 캡티브 포털 엔드포인트
│   └── static/index.html              ← AP 배너 + 네트워크 카드 + Wi-Fi 패널
└── deploy/
    ├── auto_ap.sh                     ← 부팅 자동 AP + iptables + dnsmasq
    ├── visionguide-auto-ap.service    ← oneshot systemd 유닛
    ├── visionguide-network.sudoers    ← NOPASSWD 규칙
    └── visionguide-avahi.service      ← mDNS HTTP 광고
```
