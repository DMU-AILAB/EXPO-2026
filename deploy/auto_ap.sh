#!/bin/bash
# 부팅 시 wlan0에 연결된 네트워크가 없으면 VisionGuide-AP로 자동 전환 후
# 캡티브 포털(자동 팝업)용 iptables + dnsmasq 설정을 적용한다.
# visionguide-auto-ap.service (Type=oneshot)에서 호출됨.

AP_IP="192.168.4.1"
AP_IFACE="wlan0"
DASHBOARD_PORT="5000"
DNSMASQ_CONF="/etc/NetworkManager/dnsmasq.d/visionguide-captive.conf"

apply_captive_portal() {
  echo "[auto-ap] 캡티브 포털 설정 적용..."

  # dnsmasq: 모든 DNS 쿼리를 Pi IP로 응답 (AP 클라이언트가 어떤 주소를 조회해도 Pi로 옴)
  cat > "$DNSMASQ_CONF" << EOF
# VisionGuide 캡티브 포털 — AP 모드에서 모든 DNS를 Pi IP로 응답
address=/#/${AP_IP}
EOF
  # NM의 dnsmasq 재적용 (NetworkManager 재시작 없이 dnsmasq 프로세스에 SIGHUP)
  pkill -HUP -f "dnsmasq.*NetworkManager" 2>/dev/null || true

  # iptables: AP 클라이언트의 HTTP(80)를 대시보드 포트(5000)로 리다이렉트
  # 중복 방지: 이미 규칙이 있으면 추가하지 않음
  if ! iptables -t nat -C PREROUTING -i "$AP_IFACE" -p tcp --dport 80 \
       -j REDIRECT --to-port "$DASHBOARD_PORT" 2>/dev/null; then
    iptables -t nat -A PREROUTING -i "$AP_IFACE" -p tcp --dport 80 \
      -j REDIRECT --to-port "$DASHBOARD_PORT"
    echo "[auto-ap] iptables HTTP→${DASHBOARD_PORT} 리다이렉트 적용"
  fi
}

remove_captive_portal() {
  echo "[auto-ap] 캡티브 포털 설정 제거..."
  rm -f "$DNSMASQ_CONF"
  pkill -HUP -f "dnsmasq.*NetworkManager" 2>/dev/null || true
  iptables -t nat -D PREROUTING -i "$AP_IFACE" -p tcp --dport 80 \
    -j REDIRECT --to-port "$DASHBOARD_PORT" 2>/dev/null || true
}

sleep 15
CONN=$(nmcli -g GENERAL.CONNECTION device show "$AP_IFACE" 2>/dev/null)

if [ -z "$CONN" ] || [ "$CONN" = "--" ]; then
  echo "[auto-ap] 네트워크 미연결 — VisionGuide-AP로 전환합니다"
  remove_captive_portal  # 이전 규칙 정리
  nmcli connection up VisionGuide-AP
  sleep 3
  apply_captive_portal
elif [ "$CONN" = "VisionGuide-AP" ]; then
  echo "[auto-ap] 이미 AP 모드 — 캡티브 포털 설정 재적용"
  apply_captive_portal
else
  echo "[auto-ap] 이미 연결됨: $CONN — 캡티브 포털 설정 제거"
  remove_captive_portal
fi
