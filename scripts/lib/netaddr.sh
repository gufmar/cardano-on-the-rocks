#!/usr/bin/env bash
# Stable management addressing for the three RockPis.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
#
# Prefer static IPv4 from /etc/cot-r/device.env (COT_R_RESERVED_IPV4) so addresses
# survive bridging and DHCP client-id changes. mAP DHCP pool should exclude these.

COT_R_GATEWAY_DEFAULT="172.27.213.1"
COT_R_DNS_DEFAULT="172.27.213.1"
COT_R_PREFIX_DEFAULT="24"
COT_R_NETWORKD_END0_STATIC="${COT_R_NETWORKD_DIR:-/etc/systemd/network}/00-cot-r-end0-static.network"
COT_R_DEVICE_MACS_MAP="${COT_R_DEVICE_MACS_MAP:-${COT_R_ETC_DIR}/device-macs.map}"
COT_R_KNOWN_RESERVED_IPV4S=(172.27.213.194 172.27.213.195 172.27.213.196)

cot_r_load_device_net() {
  COT_R_RESERVED_IPV4="${COT_R_RESERVED_IPV4:-}"
  COT_R_GATEWAY="${COT_R_GATEWAY:-$COT_R_GATEWAY_DEFAULT}"
  COT_R_DNS="${COT_R_DNS:-$COT_R_DNS_DEFAULT}"
  COT_R_PREFIX="${COT_R_PREFIX:-$COT_R_PREFIX_DEFAULT}"
  COT_R_DEVICE_ID="${COT_R_DEVICE_ID:-}"
  if [[ -f "${COT_R_ETC_DIR}/device.env" ]]; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck disable=SC1091
    source "${COT_R_ETC_DIR}/device.env" 2>/dev/null || true
    set +a
  fi
  COT_R_GATEWAY="${COT_R_GATEWAY:-$COT_R_GATEWAY_DEFAULT}"
  COT_R_DNS="${COT_R_DNS:-$COT_R_DNS_DEFAULT}"
  COT_R_PREFIX="${COT_R_PREFIX:-$COT_R_PREFIX_DEFAULT}"
}

cot_r_normalize_mac() {
  local mac="${1:-}"
  mac="$(printf '%s' "$mac" | tr 'A-F' 'a-f' | tr -d '[:space:]')"
  printf '%s\n' "$mac"
}

cot_r_end0_mac() {
  local mac=""
  if [[ -r /sys/class/net/end0/address ]]; then
    mac="$(cot_r_normalize_mac "$(cat /sys/class/net/end0/address)")"
  fi
  [[ -n "$mac" ]] || return 1
  printf '%s\n' "$mac"
}

cot_r_is_known_reserved_ipv4() {
  local ip="$1" x
  for x in "${COT_R_KNOWN_RESERVED_IPV4S[@]}"; do
    [[ "$ip" == "$x" ]] && return 0
  done
  return 1
}

cot_r_default_device_id_for_ip() {
  case "$1" in
    172.27.213.195) printf 'rockpi-1\n' ;;
    172.27.213.196) printf 'rockpi-2\n' ;;
    172.27.213.194) printf 'rockpi-3\n' ;;
    *) printf 'rockpi-unset\n' ;;
  esac
}

cot_r_lookup_mac_map() {
  # Prints: <ipv4> <device-id> when this end0 MAC is listed in the map.
  local mac map="${COT_R_DEVICE_MACS_MAP}"
  local line m ip id
  mac="$(cot_r_end0_mac)" || return 1
  [[ -f "$map" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -n "$line" ]] || continue
    # shellcheck disable=SC2086
    set -- $line
    m="$(cot_r_normalize_mac "${1:-}")"
    ip="${2:-}"
    id="${3:-}"
    if [[ "$m" == "$mac" && -n "$ip" ]]; then
      [[ -n "$id" ]] || id="$(cot_r_default_device_id_for_ip "$ip")"
      printf '%s %s\n' "$ip" "$id"
      return 0
    fi
  done <"$map"
  return 1
}

cot_r_reserved_ipv4() {
  cot_r_load_device_net
  local ip="${COT_R_RESERVED_IPV4:-}"
  if [[ -z "$ip" || "$ip" == "172.27.213.0" || "$ip" == "0.0.0.0" ]]; then
    return 1
  fi
  printf '%s\n' "$ip"
}

cot_r_prompt_reserved_identity() {
  # Interactive selection. Sets COT_R_RESOLVED_IPV4 / COT_R_RESOLVED_DEVICE_ID.
  local mac choice ip id
  mac="$(cot_r_end0_mac 2>/dev/null || echo unknown)"
  echo >&2
  cot_r_warn "No reserved management IP for this board yet."
  echo "  end0 MAC: ${mac}" >&2
  echo "  Choose the fixed address for THIS RockPi (do not reuse another board's IP):" >&2
  echo "    1) 172.27.213.195  rockpi-1" >&2
  echo "    2) 172.27.213.196  rockpi-2" >&2
  echo "    3) 172.27.213.194  rockpi-3" >&2
  echo "    s) skip static addressing for now" >&2
  if [[ ! -t 0 ]]; then
    cot_r_warn "stdin is not a TTY; cannot prompt. Pass --reserved-ipv4 or add this MAC to device-macs.map"
    return 1
  fi
  while true; do
    read -r -p "Select [1/2/3/s]: " choice || return 1
    case "$choice" in
      1) ip=172.27.213.195; id=rockpi-1; break ;;
      2) ip=172.27.213.196; id=rockpi-2; break ;;
      3) ip=172.27.213.194; id=rockpi-3; break ;;
      s|S|skip) return 1 ;;
      *) echo "Invalid choice" >&2 ;;
    esac
  done
  COT_R_RESOLVED_IPV4="$ip"
  COT_R_RESOLVED_DEVICE_ID="$id"
  return 0
}

cot_r_parse_prepare_identity_args() {
  # Consumes prepare CLI flags into COT_R_CLI_RESERVED_IPV4 / COT_R_CLI_DEVICE_ID.
  COT_R_CLI_RESERVED_IPV4="${COT_R_CLI_RESERVED_IPV4:-}"
  COT_R_CLI_DEVICE_ID="${COT_R_CLI_DEVICE_ID:-}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --reserved-ipv4|--ip)
        COT_R_CLI_RESERVED_IPV4="${2:-}"
        [[ -n "${COT_R_CLI_RESERVED_IPV4}" ]] || die "$1 requires an IPv4 argument"
        shift 2
        ;;
      --device-id)
        COT_R_CLI_DEVICE_ID="${2:-}"
        [[ -n "${COT_R_CLI_DEVICE_ID}" ]] || die "--device-id requires a value"
        shift 2
        ;;
      --help|-h)
        cat <<'EOF'
prepare identity options:
  --reserved-ipv4 <ip>   Fixed management IPv4 (.194 / .195 / .196)
  --ip <ip>              Alias for --reserved-ipv4
  --device-id <name>     Logical name (rockpi-1 / rockpi-2 / rockpi-3)
EOF
        exit 0
        ;;
      *)
        die "Unknown prepare option: $1 (try --reserved-ipv4 / --device-id)"
        ;;
    esac
  done
}

cot_r_ensure_device_identity() {
  # Resolve and persist this board's reserved IP before any static network write.
  # Priority: CLI flags > MAC map > existing device.env > interactive prompt.
  require_root
  local mac map_line map_ip map_id existing_ip existing_id ip id
  COT_R_RESOLVED_IPV4=""
  COT_R_RESOLVED_DEVICE_ID=""

  mac="$(cot_r_end0_mac 2>/dev/null || true)"
  cot_r_info "end0 MAC: ${mac:-unknown}"

  if [[ -n "${COT_R_CLI_RESERVED_IPV4:-}" ]]; then
    ip="${COT_R_CLI_RESERVED_IPV4}"
    id="${COT_R_CLI_DEVICE_ID:-$(cot_r_default_device_id_for_ip "$ip")}"
    cot_r_info "Identity from CLI: ${id} ${ip}"
  elif map_line="$(cot_r_lookup_mac_map)"; then
    map_ip="${map_line%% *}"
    map_id="${map_line#* }"
    ip="$map_ip"
    id="$map_id"
    cot_r_ok "Identity from MAC map: ${id} ${ip}"
  else
    cot_r_load_device_net
    existing_ip="${COT_R_RESERVED_IPV4:-}"
    existing_id="${COT_R_DEVICE_ID:-}"
    if [[ -n "$existing_ip" ]] && cot_r_is_known_reserved_ipv4 "$existing_ip"; then
      ip="$existing_ip"
      id="${existing_id:-$(cot_r_default_device_id_for_ip "$ip")}"
      if [[ -n "$mac" ]]; then
        cot_r_warn "MAC ${mac} is not in ${COT_R_DEVICE_MACS_MAP}"
      fi
      # Confirm on TTY: a previous bug seeded every board with rockpi-2 /.196.
      if [[ -t 0 ]]; then
        echo >&2
        echo "  Current device.env: ${id} @ ${ip}" >&2
        echo "  Keep it? [Y]es / [n]o (re-select) / [1]=.195 rockpi-1 / [2]=.196 rockpi-2 / [3]=.194 rockpi-3" >&2
        local keep=""
        read -r -p "Choice [Y/n/1/2/3]: " keep || keep=Y
        case "$keep" in
          ""|Y|y|yes|YES) cot_r_info "Keeping existing identity: ${id} ${ip}" ;;
          1) ip=172.27.213.195; id=rockpi-1 ;;
          2) ip=172.27.213.196; id=rockpi-2 ;;
          3) ip=172.27.213.194; id=rockpi-3 ;;
          n|N|no|NO)
            if cot_r_prompt_reserved_identity; then
              ip="${COT_R_RESOLVED_IPV4}"
              id="${COT_R_RESOLVED_DEVICE_ID}"
            else
              return 1
            fi
            ;;
          *) cot_r_warn "Unrecognized choice; keeping ${id} ${ip}" ;;
        esac
      else
        cot_r_info "Identity from existing device.env: ${id} ${ip}"
      fi
    elif cot_r_prompt_reserved_identity; then
      ip="${COT_R_RESOLVED_IPV4}"
      id="${COT_R_RESOLVED_DEVICE_ID}"
      cot_r_ok "Identity from prompt: ${id} ${ip}"
    else
      cot_r_warn "No reserved IP selected; skipping static management addressing"
      cot_r_warn "Fix later: sudo ./scripts/cot-r prepare --reserved-ipv4 172.27.213.195 --device-id rockpi-1"
      cot_r_warn "Or: add end0 MAC to ${COT_R_DEVICE_MACS_MAP} then re-run prepare"
      return 1
    fi
  fi

  if ! cot_r_is_known_reserved_ipv4 "$ip"; then
    cot_r_warn "Address ${ip} is outside documented RockPi reservations (.194/.195/.196)"
  fi

  # If MAC map disagrees with CLI/env, MAC map already won above unless CLI set.
  # When CLI set, still warn on map conflict.
  if [[ -n "${COT_R_CLI_RESERVED_IPV4:-}" ]] && map_line="$(cot_r_lookup_mac_map)"; then
    map_ip="${map_line%% *}"
    if [[ "$map_ip" != "$ip" ]]; then
      cot_r_warn "CLI IP ${ip} differs from MAC map ${map_ip} for ${mac}; using CLI as requested"
    fi
  fi

  cot_r_write_device_env_address "$ip" "$id"
  # Suggest persisting MAC map entry for next reinstall.
  if [[ -n "$mac" ]] && ! cot_r_lookup_mac_map >/dev/null 2>&1; then
    cot_r_warn "Consider adding to ${COT_R_DEVICE_MACS_MAP}:"
    echo "  ${mac} ${ip} ${id}" >&2
  fi
  return 0
}

cot_r_mgmt_ipv4_for_ssid() {
  # SSID suffix must stay stable: prefer reserved address over a wrong dynamic lease.
  local ip=""
  if ip="$(cot_r_reserved_ipv4)"; then
    printf '%s\n' "$ip"
    return 0
  fi
  cot_r_lease_ipv4
}

cot_r_write_device_env_address() {
  # Ensure device.env contains a concrete reserved address (idempotent merge).
  require_root
  local ip="$1"
  local device_id="${2:-}"
  local path="${COT_R_ETC_DIR}/device.env"
  mkdir -p "${COT_R_ETC_DIR}"
  if [[ ! -f "$path" ]]; then
    install -m 0640 -o root -g "${COT_R_DEPLOY_USER}" \
      "${COT_R_CONFIG_TEMPLATES}/device.env.example" "$path"
  fi
  python3 - "$path" "$ip" "$device_id" <<'PY'
import re, sys
from pathlib import Path
path, ip, device_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8") if path.is_file() else ""
def upsert(text, key, value):
    if not value:
        return text
    line = f"{key}={value}"
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    if pat.search(text):
        return pat.sub(line, text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"
text = upsert(text, "COT_R_RESERVED_IPV4", ip)
text = upsert(text, "COT_R_GATEWAY", "172.27.213.1")
text = upsert(text, "COT_R_DNS", "172.27.213.1")
text = upsert(text, "COT_R_PREFIX", "24")
if device_id:
    text = upsert(text, "COT_R_DEVICE_ID", device_id)
path.write_text(text, encoding="utf-8")
print(path)
PY
  chown "root:${COT_R_DEPLOY_USER}" "$path"
  chmod 0640 "$path"
  cot_r_manifest_add_file "$path" "config"
  cot_r_ok "device.env management address -> ${ip}"
}

cot_r_static_network_stanza() {
  local ifname="$1"
  local ip prefix gateway dns
  cot_r_load_device_net
  ip="$(cot_r_reserved_ipv4)" || die "Set COT_R_RESERVED_IPV4 in ${COT_R_ETC_DIR}/device.env first"
  prefix="${COT_R_PREFIX}"
  gateway="${COT_R_GATEWAY}"
  dns="${COT_R_DNS}"
  cat <<EOF
[Match]
Name=${ifname}

[Network]
Address=${ip}/${prefix}
Gateway=${gateway}
DNS=${dns}
LinkLocalAddressing=ipv6
ConfigureWithoutCarrier=yes
EOF
}

cot_r_write_end0_static_networkd() {
  # Used when AP is disabled: static on end0, beating Armbian DHCP catch-all.
  require_root
  cot_r_load_device_net
  local ip
  ip="$(cot_r_reserved_ipv4)" || die "Set COT_R_RESERVED_IPV4 in device.env"
  mkdir -p "${COT_R_NETWORKD_DIR:-/etc/systemd/network}"
  {
    echo "# Generated by cot-r. Static management address (no DHCP client-id churn)."
    echo "# SPDX-License-Identifier: AGPL-3.0-only"
    cot_r_static_network_stanza end0
  } >"${COT_R_NETWORKD_END0_STATIC}"
  chmod 0644 "${COT_R_NETWORKD_END0_STATIC}"
  cot_r_manifest_add_file "${COT_R_NETWORKD_END0_STATIC}" "networkd"
  cot_r_ok "Wrote ${COT_R_NETWORKD_END0_STATIC} (${ip})"
}

cot_r_remove_end0_static_networkd() {
  rm -f "${COT_R_NETWORKD_END0_STATIC}"
}

cot_r_cmd_net_status() {
  cot_r_load_device_net
  local reserved live_end live_br
  reserved="$(cot_r_reserved_ipv4 2>/dev/null || echo '(unset)')"
  live_end="$(ip -4 -o addr show dev end0 2>/dev/null | awk '{print $4}' | head -n1 || true)"
  live_br="$(ip -4 -o addr show dev br0 2>/dev/null | awk '{print $4}' | head -n1 || true)"
  echo "=== Management addressing ==="
  echo "reserved: ${reserved}"
  echo "gateway:  ${COT_R_GATEWAY}"
  echo "dns:      ${COT_R_DNS}"
  echo "end0:     ${live_end:-none}"
  echo "br0:      ${live_br:-none}"
  echo "ap:       $([[ -f ${COT_R_STATE_DIR}/ap-enabled ]] && echo enabled || echo disabled)"
  echo "static end0 unit: $([[ -f ${COT_R_NETWORKD_END0_STATIC} ]] && echo present || echo absent)"
  if [[ "${EUID}" -ne 0 ]] && [[ ! -r "${COT_R_ETC_DIR}/device.env" ]]; then
    echo "hint:    device.env not readable as this user; use sudo ./scripts/cot-r net-status"
  fi
  if [[ "$reserved" != "(unset)" ]]; then
    if [[ "${live_br:-}" == "${reserved}/24" || "${live_br:-}" == "${reserved}/${COT_R_PREFIX}" ]]; then
      cot_r_ok "br0 matches reserved address"
    elif [[ "${live_end:-}" == "${reserved}/24" || "${live_end:-}" == "${reserved}/${COT_R_PREFIX}" ]]; then
      cot_r_ok "end0 matches reserved address"
    else
      cot_r_warn "Live address does not match reserved ${reserved}"
    fi
  fi
}

cot_r_cmd_net_set_address() {
  require_root
  local ip="${1:-}"
  local device_id="${2:-}"
  [[ -n "$ip" ]] || die "Usage: cot-r net-set-address <ipv4> [device-id]"
  if [[ ! "$ip" =~ ^172\.27\.213\.(194|195|196)$ ]]; then
    cot_r_warn "Address ${ip} is outside the documented RockPi reservations (.194/.195/.196)"
  fi
  cot_r_write_device_env_address "$ip" "$device_id"
  # Re-apply for current mode
  if [[ -f "${COT_R_STATE_DIR}/ap-enabled" ]]; then
    cot_r_info "AP enabled: rewriting bridge static address + SSID/hostapd"
    cot_r_write_bridge_networkd
    cot_r_apply_bridge_networkd
    cot_r_write_ap_env
    cot_r_write_hostapd_conf
    systemctl restart "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true
  else
    cot_r_info "AP disabled: installing static end0 address"
    cot_r_write_end0_static_networkd
    networkctl reload || systemctl try-reload-or-restart systemd-networkd
    networkctl reconfigure end0 2>/dev/null || true
  fi
  cot_r_cmd_net_status
}
