#!/usr/bin/env bash
# Bridged Wi-Fi AP (explicit enable/disable; preserves Ethernet DHCP MAC identity).
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

COT_R_AP_PACKAGES=(hostapd iw python3-qrcode)
COT_R_HOSTAPD_UNIT="${COT_R_HOSTAPD_UNIT:-cot-r-hostapd.service}"
COT_R_NETPLAN_AP="${COT_R_NETPLAN_AP:-/etc/netplan/60-cot-r-bridge.yaml}"
# Early systemd-networkd units beat Armbian's 10-netplan-all-eth-interfaces.network
COT_R_NETWORKD_DIR="${COT_R_NETWORKD_DIR:-/etc/systemd/network}"
COT_R_NETWORKD_END0="${COT_R_NETWORKD_DIR}/00-cot-r-end0.network"
COT_R_NETWORKD_BR0_NETDEV="${COT_R_NETWORKD_DIR}/00-cot-r-br0.netdev"
COT_R_NETWORKD_BR0_NET="${COT_R_NETWORKD_DIR}/00-cot-r-br0.network"
COT_R_AP_ENABLED_FLAG="${COT_R_STATE_DIR}/ap-enabled"
COT_R_AP_ENV="${COT_R_ETC_DIR}/ap.env"
COT_R_AP_PSK_FILE_DEFAULT="${COT_R_ETC_DIR}/ap.psk"
COT_R_HOSTAPD_CONF_DEFAULT="${COT_R_ETC_DIR}/hostapd.conf"

cot_r_iw() {
  if command -v iw >/dev/null 2>&1; then
    iw "$@"
  elif [[ -x /usr/sbin/iw ]]; then
    /usr/sbin/iw "$@"
  else
    return 127
  fi
}

cot_r_ap_load_env() {
  COT_R_AP_SSID="${COT_R_AP_SSID:-}"
  COT_R_AP_PSK_FILE="${COT_R_AP_PSK_FILE:-$COT_R_AP_PSK_FILE_DEFAULT}"
  COT_R_AP_CHANNEL="${COT_R_AP_CHANNEL:-6}"
  COT_R_WIFI_COUNTRY="${COT_R_WIFI_COUNTRY:-CH}"
  COT_R_BRIDGE="${COT_R_BRIDGE:-br0}"
  COT_R_ETH_IF="${COT_R_ETH_IF:-end0}"
  COT_R_WLAN_IF="${COT_R_WLAN_IF:-wlan0}"
  COT_R_HOSTAPD_CONF="${COT_R_HOSTAPD_CONF:-$COT_R_HOSTAPD_CONF_DEFAULT}"
  if [[ -f "${COT_R_AP_ENV}" ]]; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck disable=SC1091
    if ! source "${COT_R_AP_ENV}"; then
      cot_r_warn "Could not source ${COT_R_AP_ENV} (check quoting around SSID); using derived defaults"
    fi
    set +a
  fi
  COT_R_AP_PSK_FILE="${COT_R_AP_PSK_FILE:-$COT_R_AP_PSK_FILE_DEFAULT}"
  COT_R_HOSTAPD_CONF="${COT_R_HOSTAPD_CONF:-$COT_R_HOSTAPD_CONF_DEFAULT}"
}

cot_r_eth_mac() {
  local ifc="${1:-end0}"
  local mac=""
  if [[ -r "/sys/class/net/${ifc}/address" ]]; then
    mac="$(tr -d '[:space:]' <"/sys/class/net/${ifc}/address")"
  fi
  printf '%s\n' "$mac"
}

cot_r_lease_ipv4() {
  # Live address on Ethernet, then bridge (after AP enable).
  local ip=""
  ip="$(ip -4 -o addr show dev end0 2>/dev/null | awk '{print $4}' | head -n1 | cut -d/ -f1 || true)"
  if [[ -z "$ip" ]]; then
    ip="$(ip -4 -o addr show dev br0 2>/dev/null | awk '{print $4}' | head -n1 | cut -d/ -f1 || true)"
  fi
  printf '%s\n' "$ip"
}

cot_r_board_index() {
  # Map reserved IP / device id -> 1|2|3 for short OLED-friendly SSIDs.
  local ip last id=""
  if [[ -f "${COT_R_ETC_DIR}/device.env" ]]; then
    # shellcheck disable=SC1090
    # shellcheck disable=SC1091
    source "${COT_R_ETC_DIR}/device.env" 2>/dev/null || true
    id="${COT_R_DEVICE_ID:-}"
  fi
  case "$id" in
    rockpi-1|RockPi-1|1) printf '1\n'; return 0 ;;
    rockpi-2|RockPi-2|2) printf '2\n'; return 0 ;;
    rockpi-3|RockPi-3|3) printf '3\n'; return 0 ;;
  esac
  if declare -F cot_r_mgmt_ipv4_for_ssid >/dev/null 2>&1; then
    ip="$(cot_r_mgmt_ipv4_for_ssid)"
  else
    ip="$(cot_r_lease_ipv4)"
  fi
  last="${ip##*.}"
  case "$last" in
    195) printf '1\n' ;;
    196) printf '2\n' ;;
    194) printf '3\n' ;;
    *) printf '?\n' ;;
  esac
}

cot_r_default_ssid() {
  # Short SSID so WIFI: QR fits 128x64 at >=2px modules.
  # Example: rockpi-2 / .196 -> "Cardano-Rocks 2"
  local override="${COT_R_AP_SSID_OVERRIDE:-}" idx
  if [[ -z "$override" && -f "${COT_R_ETC_DIR}/device.env" ]]; then
    # shellcheck disable=SC1090
    # shellcheck disable=SC1091
    source "${COT_R_ETC_DIR}/device.env" 2>/dev/null || true
    override="${COT_R_AP_SSID_OVERRIDE:-}"
  fi
  if [[ -n "$override" ]]; then
    printf '%s\n' "$override"
    return 0
  fi
  idx="$(cot_r_board_index)"
  printf 'Cardano-Rocks %s\n' "$idx"
}

cot_r_phy_supports_ap() {
  cot_r_iw phy 2>/dev/null | awk '
    /Supported interface modes:/ {inmodes=1; next}
    inmodes && /^[[:space:]]*\*/ { if ($0 ~ / AP$/) found=1 }
    inmodes && /^[[:space:]]*Band / {inmodes=0}
    END { exit found ? 0 : 1 }
  '
}

cot_r_secret_file_status() {
  # Distinguish missing vs present-but-unreadable for non-root callers.
  local path="$1"
  local hint="${2:-need root}"
  if [[ -r "$path" && -s "$path" ]]; then
    printf 'present'
    return 0
  fi
  if [[ -r "$path" && ! -s "$path" ]]; then
    printf 'empty'
    return 0
  fi
  if [[ "${EUID}" -ne 0 ]]; then
    # Directory may be 0750 root:cot-r; mgu cannot stat files inside.
    if [[ -d "${COT_R_ETC_DIR}" ]] && ! [[ -r "${COT_R_ETC_DIR}" || -x "${COT_R_ETC_DIR}" ]]; then
      printf 'protected (%s)' "$hint"
      return 0
    fi
    if [[ -e "$path" ]] && ! [[ -r "$path" ]]; then
      printf 'protected (%s)' "$hint"
      return 0
    fi
  fi
  printf 'missing'
}

cot_r_wifi_qr_payload() {
  local ssid="$1"
  local psk="$2"
  python3 - "$ssid" "$psk" <<'PY'
import sys

def wifi_escape(value: str) -> str:
    out = []
    for ch in value:
        if ch in ("\\", ";", ",", '"', ":"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)

ssid, psk = sys.argv[1], sys.argv[2]
print(f"WIFI:T:WPA;S:{wifi_escape(ssid)};P:{wifi_escape(psk)};H:false;;")
PY
}

cot_r_print_wifi_qr_ascii() {
  local ssid="$1"
  local psk="$2"
  if ! python3 -c 'import qrcode' >/dev/null 2>&1; then
    cot_r_warn "python3-qrcode not installed; skip ASCII QR (apt install python3-qrcode or re-run ap-prepare)"
    return 0
  fi
  python3 - "$ssid" "$psk" <<'PY'
import sys
import qrcode

def wifi_escape(value: str) -> str:
    out = []
    for ch in value:
        if ch in ("\\", ";", ",", '"', ":"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)

ssid, psk = sys.argv[1], sys.argv[2]
payload = f"WIFI:T:WPA;S:{wifi_escape(ssid)};P:{wifi_escape(psk)};H:false;;"
qr = qrcode.QRCode(
    error_correction=qrcode.constants.ERROR_CORRECT_M,
    border=1,
    box_size=1,
)
qr.add_data(payload)
qr.make(fit=True)
print("WIFI_QR_PAYLOAD=" + payload)
print("--- scan with phone camera / Wi-Fi QR reader ---")
qr.print_ascii(invert=True)
print("--- end QR ---")
PY
}

cot_r_display_wifi_qr() {
  # Best-effort OLED QR via cot-r-display. Dense WIFI payloads may fall back to text.
  local ssid="$1"
  local psk="$2"
  local sock="${COT_R_DISPLAY_SOCK:-/run/cot-r/display.sock}"
  local payload
  [[ -S "$sock" ]] || {
    cot_r_warn "Display socket missing (${sock}); skip OLED QR"
    return 0
  }
  payload="$(cot_r_wifi_qr_payload "$ssid" "$psk")" || return 0
  python3 - "$sock" "$payload" "$ssid" <<'PY'
import json, socket, sys
sock_path, payload, ssid = sys.argv[1], sys.argv[2], sys.argv[3]
msg = {
    "cmd": "qr",
    "priority": 70,
    "ttl_ms": 120000,
    "payload": {"data": payload},
}
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(sock_path)
        s.sendall((json.dumps(msg) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    reply = json.loads(data.decode().split("\n", 1)[0]) if data else {}
except OSError as e:
    print(f"display unavailable: {e}", file=sys.stderr)
    sys.exit(0)
print(json.dumps(reply))
if not reply.get("ok"):
    # Fallback: short text if QR rejected as too dense
    msg2 = {
        "cmd": "text",
        "priority": 70,
        "ttl_ms": 60000,
        "payload": {"lines": ["Join Wi-Fi", ssid[:21], "ap-show-psk"], "align": "left"},
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(sock_path)
            s.sendall((json.dumps(msg2) + "\n").encode())
    except OSError:
        pass
PY
}

cot_r_cmd_ap_show_psk() {
  require_root
  cot_r_ap_load_env
  local ssid psk no_qr=0 no_oled=0
  local arg
  for arg in "$@"; do
    case "$arg" in
      --no-qr) no_qr=1 ;;
      --no-oled) no_oled=1 ;;
      *) die "Unknown option: ${arg} (use --no-qr / --no-oled)" ;;
    esac
  done
  ssid="$(cot_r_default_ssid)"
  [[ -s "${COT_R_AP_PSK_FILE}" ]] || die "Missing ${COT_R_AP_PSK_FILE}; run sudo ./scripts/cot-r ap-prepare"
  psk="$(tr -d '[:space:]' <"${COT_R_AP_PSK_FILE}")"
  echo "SSID=${ssid}"
  echo "PSK=${psk}"
  echo "file=${COT_R_AP_PSK_FILE}"
  if [[ "$no_qr" -eq 0 ]]; then
    echo
    cot_r_print_wifi_qr_ascii "$ssid" "$psk"
  fi
  if [[ "$no_oled" -eq 0 ]]; then
    echo
    cot_r_info "Pushing Wi-Fi QR to OLED"
    cot_r_display_wifi_qr "$ssid" "$psk" || true
  fi
}

cot_r_cmd_ap_status() {
  cot_r_ap_load_env
  local mac ssid
  mac="$(cot_r_eth_mac "${COT_R_ETH_IF}")"
  ssid="$(cot_r_default_ssid)"
  echo "=== AP / bridge status ==="
  echo "eth:     ${COT_R_ETH_IF} mac=${mac}"
  echo "wlan:    ${COT_R_WLAN_IF} $(ip -br link show "${COT_R_WLAN_IF}" 2>/dev/null || echo missing)"
  echo "bridge:  ${COT_R_BRIDGE} $(ip -br link show "${COT_R_BRIDGE}" 2>/dev/null || echo '(absent)')"
  echo "ssid:    ${ssid}"
  echo "channel: ${COT_R_AP_CHANNEL}"
  echo "country: ${COT_R_WIFI_COUNTRY}"
  echo "psk:     $(cot_r_secret_file_status "${COT_R_AP_PSK_FILE}" "sudo ./scripts/cot-r ap-show-psk")"
  echo "hostapd: ${COT_R_HOSTAPD_CONF} $(cot_r_secret_file_status "${COT_R_HOSTAPD_CONF}" "need root")"
  echo "networkd: end0-port=$([[ -f "${COT_R_NETWORKD_END0}" ]] && echo present || echo missing) br0=$([[ -f "${COT_R_NETWORKD_BR0_NET}" ]] && echo present || echo missing)"
  echo "flag:    ${COT_R_AP_ENABLED_FLAG} $([[ -f "${COT_R_AP_ENABLED_FLAG}" ]] && echo enabled || echo disabled)"
  if cot_r_bridge_has_eth_port; then
    cot_r_ok "${COT_R_ETH_IF} enslaved to ${COT_R_BRIDGE}"
  elif [[ -f "${COT_R_AP_ENABLED_FLAG}" ]]; then
    cot_r_warn "${COT_R_ETH_IF} NOT enslaved to ${COT_R_BRIDGE} (clients will not get DHCP); re-run: sudo ./scripts/cot-r ap-enable --confirm"
  fi
  if [[ "${EUID}" -ne 0 ]]; then
    echo "hint:    sudo ./scripts/cot-r ap-show-psk   # print SSID + PSK + Wi-Fi QR"
  fi
  if systemctl list-unit-files "${COT_R_HOSTAPD_UNIT}" >/dev/null 2>&1; then
    echo "unit:    $(systemctl is-enabled "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true) / $(systemctl is-active "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true)"
  else
    echo "unit:    not installed"
  fi
  if cot_r_phy_supports_ap; then
    cot_r_ok "Wi-Fi PHY advertises AP mode"
  else
    cot_r_warn "Wi-Fi PHY AP mode not detected (install iw / check driver)"
  fi
  echo "addresses:"
  ip -br addr | sed 's/^/  /'
  echo "routes:"
  ip route | sed 's/^/  /' || true
  if [[ -f "${COT_R_AP_ENABLED_FLAG}" ]]; then
    local br_mac
    br_mac="$(cot_r_eth_mac "${COT_R_BRIDGE}")"
    if [[ -n "$mac" && -n "$br_mac" && "$mac" == "$br_mac" ]]; then
      cot_r_ok "Bridge MAC matches ${COT_R_ETH_IF} (${mac})"
    elif [[ -n "$br_mac" ]]; then
      cot_r_warn "Bridge MAC ${br_mac} differs from ${COT_R_ETH_IF} ${mac} (DHCP reservation risk)"
    fi
    if ip -4 -o addr show dev "${COT_R_BRIDGE}" 2>/dev/null | grep -q '172\.27\.213\.'; then
      cot_r_ok "Bridge has 172.27.213.x address"
    else
      cot_r_warn "Bridge missing 172.27.213.x address"
    fi
  fi
}

cot_r_generate_psk() {
  # 10 unambiguous chars: short to type, and WIFI: QR fits OLED with Cardano-Rocks N.
  local dest="${1:-$COT_R_AP_PSK_FILE_DEFAULT}"
  local force="${2:-}"
  mkdir -p "$(dirname "$dest")"
  if [[ -s "$dest" && "$force" != "--force" ]]; then
    cot_r_info "AP PSK already present at ${dest}"
    return 0
  fi
  if [[ -s "$dest" && "$force" == "--force" ]]; then
    backup_file "$dest" >/dev/null 2>&1 || true
  fi
  python3 - "$dest" <<'PY'
import secrets, sys
from pathlib import Path
path = Path(sys.argv[1])
# No 0/O/1/l/I; no punctuation (shell/hostapd + easy to read aloud).
alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
psk = "".join(secrets.choice(alphabet) for _ in range(10))
path.write_text(psk + "\n", encoding="utf-8")
path.chmod(0o640)
print(path)
PY
  chown "root:${COT_R_DEPLOY_USER}" "$dest" 2>/dev/null || true
  cot_r_ok "Generated AP PSK at ${dest} (10 chars)"
  cot_r_manifest_add_file "$dest" "secret"
}

cot_r_shell_quote() {
  # Single-quote for safe `source` of values that may contain spaces.
  local s="$1"
  s="${s//\'/\'\\\'\'}"
  printf "'%s'" "$s"
}

cot_r_write_ap_env() {
  local ssid
  ssid="$(cot_r_default_ssid)"
  mkdir -p "${COT_R_ETC_DIR}"
  cat >"${COT_R_AP_ENV}" <<EOF
# Generated by cot-r ap-prepare. PSK lives in ap.psk, not here.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
COT_R_AP_SSID=$(cot_r_shell_quote "$ssid")
COT_R_AP_PSK_FILE=$(cot_r_shell_quote "${COT_R_AP_PSK_FILE_DEFAULT}")
COT_R_AP_CHANNEL=$(cot_r_shell_quote "${COT_R_AP_CHANNEL:-6}")
COT_R_WIFI_COUNTRY=$(cot_r_shell_quote "${COT_R_WIFI_COUNTRY:-CH}")
COT_R_BRIDGE=$(cot_r_shell_quote "${COT_R_BRIDGE:-br0}")
COT_R_ETH_IF=$(cot_r_shell_quote "${COT_R_ETH_IF:-end0}")
COT_R_WLAN_IF=$(cot_r_shell_quote "${COT_R_WLAN_IF:-wlan0}")
COT_R_HOSTAPD_CONF=$(cot_r_shell_quote "${COT_R_HOSTAPD_CONF_DEFAULT}")
EOF
  chown "root:${COT_R_DEPLOY_USER}" "${COT_R_AP_ENV}"
  chmod 0640 "${COT_R_AP_ENV}"
  cot_r_manifest_add_file "${COT_R_AP_ENV}" "config"
  cot_r_ok "Wrote ${COT_R_AP_ENV} (ssid=${ssid})"
}

cot_r_write_hostapd_conf() {
  cot_r_ap_load_env
  local ssid psk
  ssid="$(cot_r_default_ssid)"
  [[ -s "${COT_R_AP_PSK_FILE}" ]] || die "Missing AP PSK file ${COT_R_AP_PSK_FILE}; run ap-prepare first"
  psk="$(tr -d '[:space:]' <"${COT_R_AP_PSK_FILE}")"
  [[ -n "$psk" ]] || die "Empty AP PSK"
  # hostapd passphrase must be 8..63 characters
  if [[ ${#psk} -lt 8 || ${#psk} -gt 63 ]]; then
    die "AP PSK length ${#psk} invalid for WPA-PSK (need 8..63)"
  fi
  mkdir -p "${COT_R_ETC_DIR}"
  if [[ -f "${COT_R_HOSTAPD_CONF}" ]]; then
    backup_file "${COT_R_HOSTAPD_CONF}" >/dev/null
  fi
  cat >"${COT_R_HOSTAPD_CONF}" <<EOF
# Generated by cot-r. Do not commit. SPDX-License-Identifier: AGPL-3.0-only
interface=${COT_R_WLAN_IF}
driver=nl80211
bridge=${COT_R_BRIDGE}

ssid=${ssid}
country_code=${COT_R_WIFI_COUNTRY}
ieee80211d=1
hw_mode=g
channel=${COT_R_AP_CHANNEL}
ieee80211n=1
wmm_enabled=1

auth_algs=1
ignore_broadcast_ssid=0

# Allow cot-r mgmt to list associated STAs (onboarding step 1→2).
ctrl_interface=/run/hostapd
ctrl_interface_group=${COT_R_DEPLOY_USER}

wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=${psk}
EOF
  chown "root:${COT_R_DEPLOY_USER}" "${COT_R_HOSTAPD_CONF}"
  chmod 0640 "${COT_R_HOSTAPD_CONF}"
  cot_r_manifest_add_file "${COT_R_HOSTAPD_CONF}" "config"
  cot_r_ok "Wrote ${COT_R_HOSTAPD_CONF}"
}

cot_r_write_bridge_networkd() {
  # Armbian generates 10-netplan-all-eth-interfaces.network which matches end0
  # before any 10-netplan-end0.network from a netplan bridge fragment. That left
  # end0 with DHCP outside the bridge while only wlan0 was enslaved — Wi-Fi
  # clients associated but never got DHCP from the mAP.
  #
  # 00-cot-r-* units sort first and force the intended bridged layout.
  cot_r_ap_load_env
  local mac
  mac="$(cot_r_eth_mac "${COT_R_ETH_IF}")"
  [[ -n "$mac" ]] || die "Could not read MAC for ${COT_R_ETH_IF}"
  mkdir -p "${COT_R_NETWORKD_DIR}"

  cat >"${COT_R_NETWORKD_BR0_NETDEV}" <<EOF
# Generated by cot-r ap-enable. SPDX-License-Identifier: AGPL-3.0-only
[NetDev]
Name=${COT_R_BRIDGE}
Kind=bridge
MACAddress=${mac}
EOF

  # Static management IP on br0 (not DHCP): bridging changes DHCP client-id and
  # breaks mAP reservations that were learned against the old end0 lease identity.
  local reserved gateway dns prefix
  cot_r_load_device_net
  reserved="$(cot_r_reserved_ipv4)" || die "Set COT_R_RESERVED_IPV4 in ${COT_R_ETC_DIR}/device.env (e.g. 172.27.213.196)"
  gateway="${COT_R_GATEWAY}"
  dns="${COT_R_DNS}"
  prefix="${COT_R_PREFIX}"

  cat >"${COT_R_NETWORKD_BR0_NET}" <<EOF
# Generated by cot-r ap-enable. SPDX-License-Identifier: AGPL-3.0-only
[Match]
Name=${COT_R_BRIDGE}

[Network]
Address=${reserved}/${prefix}
Gateway=${gateway}
DNS=${dns}
LinkLocalAddressing=ipv6
ConfigureWithoutCarrier=yes
EOF

  cat >"${COT_R_NETWORKD_END0}" <<EOF
# Generated by cot-r ap-enable. SPDX-License-Identifier: AGPL-3.0-only
# Must sort before 10-netplan-all-eth-interfaces.network
[Match]
Name=${COT_R_ETH_IF}

[Network]
Bridge=${COT_R_BRIDGE}
LinkLocalAddressing=no
ConfigureWithoutCarrier=yes
EOF

  chmod 0644 "${COT_R_NETWORKD_BR0_NETDEV}" "${COT_R_NETWORKD_BR0_NET}" "${COT_R_NETWORKD_END0}"
  # Bridge mode replaces standalone end0 static unit
  cot_r_remove_end0_static_networkd 2>/dev/null || rm -f /etc/systemd/network/00-cot-r-end0-static.network
  cot_r_manifest_add_file "${COT_R_NETWORKD_BR0_NETDEV}" "networkd"
  cot_r_manifest_add_file "${COT_R_NETWORKD_BR0_NET}" "networkd"
  cot_r_manifest_add_file "${COT_R_NETWORKD_END0}" "networkd"
  cot_r_ok "Wrote bridge units (MAC=${mac}, static ${reserved}/${prefix} on ${COT_R_BRIDGE})"

  # Remove stale netplan bridge fragment if present (old approach).
  if [[ -f "${COT_R_NETPLAN_AP}" ]]; then
    backup_file "${COT_R_NETPLAN_AP}" >/dev/null
    rm -f "${COT_R_NETPLAN_AP}"
    cot_r_info "Removed ${COT_R_NETPLAN_AP} (superseded by 00-cot-r networkd units)"
  fi
}

cot_r_bridge_has_eth_port() {
  cot_r_ap_load_env
  ip -d link show "${COT_R_ETH_IF}" 2>/dev/null | grep -q "master ${COT_R_BRIDGE}"
}

cot_r_apply_bridge_networkd() {
  cot_r_info "Reloading systemd-networkd for bridged AP (may briefly blip SSH)"
  networkctl reload || systemctl try-reload-or-restart systemd-networkd || \
    systemctl restart systemd-networkd
  # Force reconfigure in a safe order
  networkctl reconfigure "${COT_R_ETH_IF}" 2>/dev/null || true
  networkctl reconfigure "${COT_R_BRIDGE}" 2>/dev/null || true
  # Drop any leftover IPv4 on the raw Ethernet port once enslaved
  if cot_r_bridge_has_eth_port; then
    ip -4 addr flush dev "${COT_R_ETH_IF}" 2>/dev/null || true
  fi
}

cot_r_wait_bridge_ipv4() {
  local i addr=""
  for i in $(seq 1 20); do
    addr="$(ip -4 -o addr show dev "${COT_R_BRIDGE}" 2>/dev/null | awk '{print $4}' | head -n1 || true)"
    if [[ -n "$addr" ]]; then
      printf '%s\n' "$addr"
      return 0
    fi
    sleep 1
  done
  return 1
}

cot_r_install_hostapd_unit() {
  local src dest
  src="${COT_R_DEPLOY_DIR}/systemd/${COT_R_HOSTAPD_UNIT}"
  dest="/etc/systemd/system/${COT_R_HOSTAPD_UNIT}"
  [[ -f "$src" ]] || die "Missing ${src}"
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    cot_r_info "hostapd unit up to date"
  else
    if [[ -f "$dest" ]]; then
      backup_file "$dest" >/dev/null
    fi
    install -m 0644 "$src" "$dest"
  fi
  systemctl daemon-reload
  cot_r_manifest_add_service "${COT_R_HOSTAPD_UNIT}"
  cot_r_manifest_add_file "$dest" "unit"
}

cot_r_ap_show_display() {
  cot_r_ap_load_env
  local ssid
  ssid="$(cot_r_default_ssid)"
  python3 - "$ssid" "${COT_R_DISPLAY_SOCK}" <<'PY' 2>/dev/null || true
import json, socket, sys
ssid, path = sys.argv[1], sys.argv[2]
msg = {
    "cmd": "text",
    "priority": 50,
    "ttl_ms": 15000,
    "payload": {"lines": ["AP ready", ssid, "join Wi-Fi", "then open UI"], "align": "left"},
}
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        s.connect(path)
        s.sendall((json.dumps(msg) + "\n").encode())
except OSError:
    pass
PY
}

cot_r_require_confirm() {
  local flag="${1:-}"
  if [[ "$flag" != "--confirm" ]]; then
    die "Refusing to change networking without --confirm (example: sudo ./scripts/cot-r ap-enable --confirm)"
  fi
}

cot_r_cmd_ap_prepare() {
  require_root
  local rotate_psk=0 arg
  for arg in "$@"; do
    case "$arg" in
      --rotate-psk|--new-psk) rotate_psk=1 ;;
      *) die "Unknown ap-prepare option: ${arg} (use --rotate-psk)" ;;
    esac
  done
  cot_r_init_logging
  cot_r_ensure_state_dirs
  cot_r_write_initial_manifest
  cot_r_info "AP prepare: packages + SSID/PSK + configs (does not apply bridge yet)"
  if ! cot_r_phy_supports_ap; then
    # iw may be missing before package install
    true
  fi
  cot_r_apt_update
  cot_r_install_packages "ap" "${COT_R_AP_PACKAGES[@]}"
  if ! cot_r_phy_supports_ap; then
    die "Wi-Fi PHY does not advertise AP mode; refusing to prepare AP"
  fi
  cot_r_ap_load_env
  if [[ ! -f "${COT_R_ETC_DIR}/ap.env.example" && -f "${COT_R_CONFIG_TEMPLATES}/ap.env.example" ]]; then
    install -m 0644 -o root -g "${COT_R_DEPLOY_USER}" \
      "${COT_R_CONFIG_TEMPLATES}/ap.env.example" "${COT_R_ETC_DIR}/ap.env.example"
  fi
  cot_r_write_ap_env
  if [[ "$rotate_psk" -eq 1 ]]; then
    cot_r_generate_psk "${COT_R_AP_PSK_FILE_DEFAULT}" --force
  else
    cot_r_generate_psk "${COT_R_AP_PSK_FILE_DEFAULT}"
    # Half-panel OLED onboarding needs <=10 char PSK for >=2px modules.
    if [[ -s "${COT_R_AP_PSK_FILE_DEFAULT}" ]]; then
      local plen
      plen="$(tr -d '[:space:]' <"${COT_R_AP_PSK_FILE_DEFAULT}" | wc -c)"
      if [[ "$plen" -gt 10 ]]; then
        cot_r_warn "Existing PSK is ${plen} chars; rotating to 10 chars so OLED step-1 QR stays scannable"
        cot_r_generate_psk "${COT_R_AP_PSK_FILE_DEFAULT}" --force
      fi
    fi
  fi
  cot_r_write_hostapd_conf
  cot_r_install_hostapd_unit
  cot_r_fix_etc_permissions 2>/dev/null || true
  if [[ -f "${COT_R_STATE_DIR}/ap-enabled" ]] && systemctl is-active --quiet "${COT_R_HOSTAPD_UNIT}"; then
    cot_r_info "AP already enabled; restarting hostapd to apply new SSID/PSK"
    systemctl restart "${COT_R_HOSTAPD_UNIT}" || cot_r_warn "hostapd restart failed"
  fi
  cot_r_ok "AP prepare complete. Review SSID/PSK, then: sudo ./scripts/cot-r ap-enable --confirm"
  cot_r_warn "ap-enable briefly reconfigures networking (bridge). Stay on Ethernet SSH; expect a short blip."
  echo "SSID=$(cot_r_default_ssid)"
  echo "PSK_FILE=${COT_R_AP_PSK_FILE_DEFAULT}"
}

cot_r_cmd_ap_enable() {
  require_root
  cot_r_require_confirm "${1:-}"
  cot_r_init_logging
  cot_r_ensure_state_dirs
  cot_r_ap_load_env
  [[ -s "${COT_R_AP_PSK_FILE}" ]] || die "Run sudo ./scripts/cot-r ap-prepare first"
  [[ -f "${COT_R_HOSTAPD_CONF}" ]] || die "Missing ${COT_R_HOSTAPD_CONF}; run ap-prepare"
  if ! cot_r_phy_supports_ap; then
    die "Wi-Fi PHY does not advertise AP mode"
  fi

  cot_r_snapshot_system "pre-ap-enable" >/dev/null
  local mac before_ip addr
  mac="$(cot_r_eth_mac "${COT_R_ETH_IF}")"
  before_ip="$(ip -4 -o addr show dev "${COT_R_ETH_IF}" 2>/dev/null | awk '{print $4}' | head -n1 || true)"
  [[ -z "$before_ip" ]] && before_ip="$(ip -4 -o addr show dev "${COT_R_BRIDGE}" 2>/dev/null | awk '{print $4}' | head -n1 || true)"
  cot_r_info "Enabling bridged AP; eth MAC=${mac} current_ip=${before_ip}"

  cot_r_write_bridge_networkd
  cot_r_install_hostapd_unit
  # If a leftover netplan bridge exists, regenerate without it
  netplan generate 2>/dev/null || true
  cot_r_apply_bridge_networkd

  if ! cot_r_bridge_has_eth_port; then
    cot_r_error "${COT_R_ETH_IF} is not enslaved to ${COT_R_BRIDGE}"
    cot_r_error "Wi-Fi clients cannot reach mAP DHCP in this state"
    die "Bridge enslavement failed; check: ip -d link show ${COT_R_ETH_IF}"
  fi
  cot_r_ok "${COT_R_ETH_IF} is a port of ${COT_R_BRIDGE}"

  if addr="$(cot_r_wait_bridge_ipv4)"; then
    cot_r_ok "${COT_R_BRIDGE} address ${addr}"
  else
    cot_r_warn "No IPv4 on ${COT_R_BRIDGE} yet after wait; DHCP may still arrive"
  fi

  systemctl enable "${COT_R_HOSTAPD_UNIT}"
  systemctl restart "${COT_R_HOSTAPD_UNIT}" || die "hostapd failed to start; check: journalctl -u ${COT_R_HOSTAPD_UNIT}"

  # hostapd adds wlan0 to the bridge; confirm
  sleep 1
  if ip -d link show "${COT_R_WLAN_IF}" 2>/dev/null | grep -q "master ${COT_R_BRIDGE}"; then
    cot_r_ok "${COT_R_WLAN_IF} is a port of ${COT_R_BRIDGE}"
  else
    cot_r_warn "${COT_R_WLAN_IF} not yet showing master ${COT_R_BRIDGE}; check hostapd logs"
  fi

  mkdir -p "${COT_R_STATE_DIR}"
  date -u '+%Y-%m-%dT%H:%M:%SZ' >"${COT_R_AP_ENABLED_FLAG}"
  chmod 0644 "${COT_R_AP_ENABLED_FLAG}"
  cot_r_manifest_add_file "${COT_R_AP_ENABLED_FLAG}" "state"

  cot_r_ap_show_display
  cot_r_snapshot_system "post-ap-enable" >/dev/null
  cot_r_ok "AP enabled (bridged). SSID=$(cot_r_default_ssid)"
  cot_r_cmd_ap_status
  cot_r_info "PSK: sudo ./scripts/cot-r ap-show-psk"
}

cot_r_cmd_ap_disable() {
  require_root
  cot_r_require_confirm "${1:-}"
  cot_r_init_logging
  cot_r_ap_load_env
  cot_r_info "Disabling AP and removing bridge networkd units"
  systemctl stop "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true
  systemctl disable "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true

  rm -f "${COT_R_NETWORKD_END0}" "${COT_R_NETWORKD_BR0_NET}" "${COT_R_NETWORKD_BR0_NETDEV}"
  if [[ -f "${COT_R_NETPLAN_AP}" ]]; then
    backup_file "${COT_R_NETPLAN_AP}" >/dev/null
    rm -f "${COT_R_NETPLAN_AP}"
  fi

  cot_r_info "Restoring Ethernet with static reserved address (not DHCP)"
  netplan generate || true
  # Remove empty bridge device if still present
  ip link set "${COT_R_BRIDGE}" down 2>/dev/null || true
  ip link delete "${COT_R_BRIDGE}" type bridge 2>/dev/null || true
  ip link set "${COT_R_WLAN_IF}" down 2>/dev/null || true
  cot_r_write_end0_static_networkd
  networkctl reload || systemctl try-reload-or-restart systemd-networkd || \
    systemctl restart systemd-networkd
  networkctl reconfigure "${COT_R_ETH_IF}" 2>/dev/null || true

  rm -f "${COT_R_AP_ENABLED_FLAG}"
  cot_r_ok "AP disabled; end0 static management address restored"
  cot_r_cmd_ap_status
  cot_r_cmd_net_status 2>/dev/null || true
}
