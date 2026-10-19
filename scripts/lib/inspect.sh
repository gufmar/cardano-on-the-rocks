#!/usr/bin/env bash
# Read-only system inspection.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

cot_r_cmd_status() {
  local arch kernel pretty nm_state snd_state eth wifi issues=0
  cot_r_init_logging
  echo "=== Cardano on the Rocks: status ==="
  echo "repo:    ${COT_R_REPO_ROOT}"
  echo "version: $(cot_r_version)"
  echo "source:  https://github.com/gufmar/cardano-on-the-rocks"
  echo

  arch="$(uname -m)"
  kernel="$(uname -r)"
  pretty="$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"
  echo "=== Platform ==="
  echo "hostname:     $(hostname)"
  echo "architecture: ${arch}"
  echo "kernel:       ${kernel}"
  echo "os:           ${pretty}"
  if [[ "$arch" != "aarch64" ]]; then
    cot_r_warn "Expected aarch64; found ${arch}"
    issues=$((issues + 1))
  else
    cot_r_ok "Architecture aarch64"
  fi
  if [[ "$kernel" != *rockchip64* ]]; then
    cot_r_warn "Kernel name does not contain rockchip64: ${kernel}"
  fi
  if [[ ! -f "${COT_R_ARMBIAN_ENV}" ]]; then
    cot_r_warn "Missing ${COT_R_ARMBIAN_ENV} (not an Armbian boot layout?)"
    issues=$((issues + 1))
  else
    echo "armbianEnv:"
    sed 's/^/  /' "${COT_R_ARMBIAN_ENV}"
    if grep -Eq '(^| )overlays=.*\brk3399-i2c7\b' "${COT_R_ARMBIAN_ENV}" 2>/dev/null \
      || grep -Eq '^overlays=.*rk3399-i2c7' "${COT_R_ARMBIAN_ENV}" 2>/dev/null; then
      cot_r_ok "Overlay rk3399-i2c7 listed in armbianEnv"
    else
      cot_r_warn "Overlay rk3399-i2c7 not yet listed (prepare will add it)"
    fi
  fi
  echo

  echo "=== Networking ==="
  snd_state="$(systemctl is-active systemd-networkd 2>/dev/null || true)"
  nm_state="$(systemctl is-active NetworkManager 2>/dev/null || true)"
  snd_state="${snd_state:-unknown}"
  nm_state="${nm_state:-unknown}"
  echo "systemd-networkd: ${snd_state}"
  echo "NetworkManager:   ${nm_state}"
  ip -br addr 2>/dev/null || true
  echo "routes:"
  ip route 2>/dev/null | sed 's/^/  /' || true
  eth="$(ip -br addr show end0 2>/dev/null || true)"
  if [[ -n "$eth" ]]; then
    if echo "$eth" | grep -q '172\.27\.213\.'; then
      cot_r_ok "end0 has 172.27.213.x address"
    else
      cot_r_warn "end0 is up but not on 172.27.213.0/24: ${eth}"
    fi
  else
    cot_r_warn "Interface end0 not found"
    issues=$((issues + 1))
  fi
  wifi="$(ip -br link show wlan0 2>/dev/null || true)"
  if [[ -n "$wifi" ]]; then
    echo "wlan0: ${wifi}"
  else
    cot_r_warn "wlan0 not present"
  fi
  if ss -tln 2>/dev/null | grep -qE ':22\s'; then
    cot_r_ok "SSH appears to be listening on :22"
  else
    cot_r_warn "Could not confirm SSH on :22"
    issues=$((issues + 1))
  fi
  echo

  echo "=== I2C / OLED ==="
  if ls /dev/i2c-* >/dev/null 2>&1; then
    ls -la /dev/i2c-* | sed 's/^/  /'
  else
    cot_r_warn "No /dev/i2c-* nodes"
  fi
  if command -v i2cdetect >/dev/null 2>&1; then
    echo "i2c adapters:"
    i2cdetect -l 2>/dev/null | sed 's/^/  /' || true
  else
    cot_r_warn "i2c-tools not installed yet (prepare installs them)"
  fi
  # Heuristic: RockPi header I2C7 often appears as a higher-numbered bus after overlay.
  if [[ -e /sys/bus/i2c/devices/i2c-7 || -e /dev/i2c-7 ]]; then
    cot_r_ok "i2c-7 node present"
  else
    cot_r_warn "i2c-7 not present yet (overlay may need reboot)"
  fi
  echo

  echo "=== Project state ==="
  if [[ -f "${COT_R_MANIFEST}" ]]; then
    echo "manifest: ${COT_R_MANIFEST}"
    if command -v jq >/dev/null 2>&1; then
      jq '{version, updated_at, packages: (.packages|length), files: (.files|length), services, overlays, users}' \
        "${COT_R_MANIFEST}" 2>/dev/null || cat "${COT_R_MANIFEST}"
    else
      cat "${COT_R_MANIFEST}"
    fi
  else
    echo "manifest: (not created; run prepare)"
  fi
  if systemctl list-unit-files "${COT_R_DISPLAY_UNIT}" >/dev/null 2>&1; then
    echo "display unit: $(systemctl is-enabled "${COT_R_DISPLAY_UNIT}" 2>/dev/null || echo n/a) / $(systemctl is-active "${COT_R_DISPLAY_UNIT}" 2>/dev/null || echo n/a)"
  else
    echo "display unit: not installed"
  fi
  if getent passwd "${COT_R_DISPLAY_USER}" >/dev/null 2>&1; then
    cot_r_ok "User ${COT_R_DISPLAY_USER} exists"
  else
    echo "user ${COT_R_DISPLAY_USER}: not created"
  fi
  echo

  echo "=== Summary ==="
  if [[ "$issues" -eq 0 ]]; then
    cot_r_ok "No hard compatibility issues detected"
  else
    cot_r_warn "Reported ${issues} issue(s); review warnings above"
  fi
  return 0
}
