#!/usr/bin/env bash
# Bridged Wi-Fi AP (explicit enable/disable; preserves Ethernet DHCP MAC identity).
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

COT_R_AP_PACKAGES=(hostapd iw)
COT_R_HOSTAPD_UNIT="${COT_R_HOSTAPD_UNIT:-cot-r-hostapd.service}"
COT_R_NETPLAN_AP="${COT_R_NETPLAN_AP:-/etc/netplan/60-cot-r-bridge.yaml}"
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
  # Prefer live lease on Ethernet, then bridge (after AP enable).
  local ip=""
  ip="$(ip -4 -o addr show dev end0 2>/dev/null | awk '{print $4}' | head -n1 | cut -d/ -f1 || true)"
  if [[ -z "$ip" ]]; then
    ip="$(ip -4 -o addr show dev br0 2>/dev/null | awk '{print $4}' | head -n1 | cut -d/ -f1 || true)"
  fi
  if [[ -z "$ip" && -f "${COT_R_ETC_DIR}/device.env" ]]; then
    # shellcheck disable=SC1090
    # shellcheck disable=SC1091
    source "${COT_R_ETC_DIR}/device.env" 2>/dev/null || true
    if [[ -n "${COT_R_RESERVED_IPV4:-}" && "${COT_R_RESERVED_IPV4}" != "172.27.213.0" ]]; then
      ip="${COT_R_RESERVED_IPV4}"
    fi
  fi
  printf '%s\n' "$ip"
}

cot_r_default_ssid() {
  # Unique per device: "Cardano-on-the-Rocks .<last-octet-of-lease>"
  # Example: 172.27.213.196 -> "Cardano-on-the-Rocks .196"
  # Optional manual override: set COT_R_AP_SSID_OVERRIDE in ap.env / device.env.
  local ip last override="${COT_R_AP_SSID_OVERRIDE:-}"
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
  ip="$(cot_r_lease_ipv4)"
  last="${ip##*.}"
  if [[ -z "$last" || "$last" == "$ip" ]]; then
    last="xxx"
  fi
  printf 'Cardano-on-the-Rocks .%s\n' "$last"
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

cot_r_cmd_ap_show_psk() {
  require_root
  cot_r_ap_load_env
  local ssid psk
  ssid="$(cot_r_default_ssid)"
  [[ -s "${COT_R_AP_PSK_FILE}" ]] || die "Missing ${COT_R_AP_PSK_FILE}; run sudo ./scripts/cot-r ap-prepare"
  psk="$(tr -d '[:space:]' <"${COT_R_AP_PSK_FILE}")"
  echo "SSID=${ssid}"
  echo "PSK=${psk}"
  echo "file=${COT_R_AP_PSK_FILE}"
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
  echo "netplan: ${COT_R_NETPLAN_AP} $([[ -f "${COT_R_NETPLAN_AP}" ]] && echo present || echo missing)"
  echo "flag:    ${COT_R_AP_ENABLED_FLAG} $([[ -f "${COT_R_AP_ENABLED_FLAG}" ]] && echo enabled || echo disabled)"
  if [[ "${EUID}" -ne 0 ]]; then
    echo "hint:    sudo ./scripts/cot-r ap-show-psk   # print SSID + PSK"
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
  local dest="${1:-$COT_R_AP_PSK_FILE_DEFAULT}"
  mkdir -p "$(dirname "$dest")"
  if [[ -s "$dest" ]]; then
    cot_r_info "AP PSK already present at ${dest}"
    return 0
  fi
  # 16 bytes -> ~22 char urlsafe; compact enough for OLED QR experiments.
  python3 - "$dest" <<'PY'
import secrets, sys
from pathlib import Path
path = Path(sys.argv[1])
# Avoid ambiguous punctuation that breaks shell/hostapd quoting.
alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
psk = "".join(secrets.choice(alphabet) for _ in range(16))
path.write_text(psk + "\n", encoding="utf-8")
path.chmod(0o640)
print(path)
PY
  chown "root:${COT_R_DEPLOY_USER}" "$dest" 2>/dev/null || true
  cot_r_ok "Generated AP PSK at ${dest}"
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

cot_r_write_netplan_bridge() {
  cot_r_ap_load_env
  local mac
  mac="$(cot_r_eth_mac "${COT_R_ETH_IF}")"
  [[ -n "$mac" ]] || die "Could not read MAC for ${COT_R_ETH_IF}"
  if [[ -f "${COT_R_NETPLAN_AP}" ]]; then
    backup_file "${COT_R_NETPLAN_AP}" >/dev/null
  fi
  cat >"${COT_R_NETPLAN_AP}" <<EOF
# Generated by cot-r ap-enable. Preserves ${COT_R_ETH_IF} MAC ${mac} on ${COT_R_BRIDGE}.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
network:
  version: 2
  renderer: networkd
  ethernets:
    ${COT_R_ETH_IF}:
      dhcp4: false
      dhcp6: false
      optional: true
  bridges:
    ${COT_R_BRIDGE}:
      macaddress: "${mac}"
      interfaces:
        - ${COT_R_ETH_IF}
      dhcp4: true
      dhcp6: false
      parameters:
        stp: false
        forward-delay: 0
EOF
  chmod 0600 "${COT_R_NETPLAN_AP}"
  cot_r_manifest_add_file "${COT_R_NETPLAN_AP}" "netplan"
  cot_r_ok "Wrote ${COT_R_NETPLAN_AP} (bridge MAC=${mac})"
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
  cot_r_generate_psk "${COT_R_AP_PSK_FILE_DEFAULT}"
  cot_r_write_hostapd_conf
  cot_r_install_hostapd_unit
  cot_r_fix_etc_permissions 2>/dev/null || true
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
  local mac before_ip
  mac="$(cot_r_eth_mac "${COT_R_ETH_IF}")"
  before_ip="$(ip -4 -o addr show dev "${COT_R_ETH_IF}" 2>/dev/null | awk '{print $4}' | head -n1 || true)"
  cot_r_info "Enabling bridged AP; eth MAC=${mac} current_ip=${before_ip}"

  cot_r_write_netplan_bridge
  cot_r_install_hostapd_unit

  # Apply netplan. This is the disruptive step; do not reboot.
  cot_r_info "Applying netplan (may briefly interrupt SSH)"
  if ! netplan generate; then
    die "netplan generate failed; bridge YAML not applied"
  fi
  if ! netplan apply; then
    cot_r_error "netplan apply failed; attempting to remove AP netplan fragment"
    rm -f "${COT_R_NETPLAN_AP}"
    netplan apply || true
    die "netplan apply failed; restored previous netplan as best-effort"
  fi

  # Wait briefly for DHCP on bridge
  local i addr=""
  for i in 1 2 3 4 5 6 7 8 9 10; do
    addr="$(ip -4 -o addr show dev "${COT_R_BRIDGE}" 2>/dev/null | awk '{print $4}' | head -n1 || true)"
    if [[ -n "$addr" ]]; then
      break
    fi
    sleep 1
  done
  if [[ -z "$addr" ]]; then
    cot_r_warn "No IPv4 on ${COT_R_BRIDGE} yet; continuing to start hostapd"
  else
    cot_r_ok "${COT_R_BRIDGE} address ${addr}"
  fi

  systemctl enable "${COT_R_HOSTAPD_UNIT}"
  systemctl restart "${COT_R_HOSTAPD_UNIT}" || die "hostapd failed to start; check: journalctl -u ${COT_R_HOSTAPD_UNIT}"

  mkdir -p "${COT_R_STATE_DIR}"
  date -u '+%Y-%m-%dT%H:%M:%SZ' >"${COT_R_AP_ENABLED_FLAG}"
  chmod 0644 "${COT_R_AP_ENABLED_FLAG}"
  cot_r_manifest_add_file "${COT_R_AP_ENABLED_FLAG}" "state"

  cot_r_ap_show_display
  cot_r_snapshot_system "post-ap-enable" >/dev/null
  cot_r_ok "AP enabled (bridged). SSID=$(cot_r_default_ssid)"
  cot_r_cmd_ap_status
  cot_r_info "PSK is in ${COT_R_AP_PSK_FILE} (not printed). Demo reset must retain it."
}

cot_r_cmd_ap_disable() {
  require_root
  cot_r_require_confirm "${1:-}"
  cot_r_init_logging
  cot_r_ap_load_env
  cot_r_info "Disabling AP and removing bridge netplan fragment"
  systemctl stop "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true
  systemctl disable "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true

  if [[ -f "${COT_R_NETPLAN_AP}" ]]; then
    backup_file "${COT_R_NETPLAN_AP}" >/dev/null
    rm -f "${COT_R_NETPLAN_AP}"
  fi
  cot_r_info "Applying netplan to restore Ethernet DHCP on ${COT_R_ETH_IF}"
  netplan generate || true
  netplan apply || cot_r_warn "netplan apply reported errors during ap-disable"

  rm -f "${COT_R_AP_ENABLED_FLAG}"
  # Bring wlan down if still up
  ip link set "${COT_R_WLAN_IF}" down 2>/dev/null || true

  cot_r_ok "AP disabled; Ethernet-only networking restored (best effort)"
  cot_r_cmd_ap_status
}
