#!/usr/bin/env bash
# prepare / reset-project orchestration.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

cot_r_cmd_prepare() {
  local reboot_line reboot_needed=no
  local identity_ok=no
  require_root
  cot_r_parse_prepare_identity_args "$@"
  cot_r_init_logging
  cot_r_info "Starting prepare (idempotent)"
  cot_r_ensure_state_dirs
  cot_r_write_initial_manifest
  cot_r_snapshot_system "pre-prepare" >/dev/null

  # 1) Compatibility gate (soft warnings already in status; hard fail on wrong arch)
  if [[ "$(uname -m)" != "aarch64" ]]; then
    die "Unsupported architecture $(uname -m); this project targets RockPi 4B aarch64"
  fi
  if [[ ! -f "${COT_R_ARMBIAN_ENV}" ]]; then
    die "Missing ${COT_R_ARMBIAN_ENV}; refusing to prepare on a non-Armbian boot layout"
  fi

  # 2) Packages
  cot_r_install_baseline_packages
  cot_r_install_display_packages

  # 3) Users / dirs / udev / identity
  cot_r_ensure_display_user
  cot_r_ensure_deploy_user
  cot_r_install_udev_rules
  cot_r_install_config_templates
  cot_r_ensure_device_id_file
  cot_r_ensure_api_token
  cot_r_ensure_peer_token
  cot_r_install_mgmt_sudoers
  cot_r_fix_etc_permissions
  # Resolve THIS board's reserved IP before any static network write.
  # Never invent .196 from a shared example template.
  if cot_r_ensure_device_identity; then
    identity_ok=yes
  fi
  # If a concrete reserved address is configured and AP is off, prefer static end0.
  if [[ "$identity_ok" == "yes" && ! -f "${COT_R_STATE_DIR}/ap-enabled" ]]; then
    if cot_r_reserved_ipv4 >/dev/null 2>&1; then
      cot_r_write_end0_static_networkd
      networkctl reload 2>/dev/null || true
      networkctl reconfigure end0 2>/dev/null || true
    fi
  fi

  # 4) I2C7 overlay (no reboot performed)
  reboot_line="$(cot_r_ensure_i2c7_overlay | tee /dev/stderr | grep '^reboot_needed=' | tail -n1 || true)"
  if [[ "$reboot_line" == "reboot_needed=yes" ]]; then
    reboot_needed=yes
  fi

  # 5) Display + management runtime
  cot_r_install_display_code
  cot_r_install_mgmt_code
  cot_r_install_display_unit

  # 6) OLED discovery / test when bus is available
  if [[ -e /dev/i2c-7 ]] || [[ -e /sys/bus/i2c/devices/i2c-7 ]]; then
    local disc
    if disc="$(cot_r_discover_oled)"; then
      local bus addr
      bus="${disc#bus=}"; bus="${bus%% *}"
      addr="${disc##*addr=}"
      cot_r_write_oled_config "$bus" "$addr"
      cot_r_oled_selftest
      systemctl try-restart "${COT_R_DISPLAY_UNIT}" 2>/dev/null || true
    else
      cot_r_warn "i2c-7 present but no SSD1306 at 0x3c/0x3d; check wiring"
    fi
  else
    cot_r_warn "I2C7 device node not present yet"
  fi

  # 7) Management API (after token + code)
  cot_r_install_mgmt_unit

  # 8) Deployment clone (separate from development checkout)
  cot_r_ensure_deploy_clone
  local head prev=""
  head="$(cot_r_git_head "${COT_R_DEPLOY_CLONE}")"
  if [[ -n "$head" ]]; then
    if [[ -f "${COT_R_DEPLOY_INFO}" ]]; then
      prev="$(python3 - "${COT_R_DEPLOY_INFO}" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("commit") or "")
except Exception:
    print("")
PY
)"
    fi
    cot_r_write_deploy_info "$head" "prepare" "$prev"
  fi

  cot_r_snapshot_system "post-prepare" >/dev/null
  echo
  cot_r_ok "Prepare completed"
  echo "Completed steps: snapshot, packages, users/udev/identity, overlay, display, management, deploy clone"
  if reserved="$(cot_r_reserved_ipv4 2>/dev/null)"; then
    echo "Reserved management IP: ${reserved} (device.env + static addressing)"
  else
    cot_r_warn "Reserved IP unset; static addressing skipped"
  fi
  echo "Management API: http://<device>:8080/api/v1/  (token in ${COT_R_API_TOKEN_FILE})"
  if [[ "$reboot_needed" == "yes" ]]; then
    cot_r_warn "ACTION REQUIRED: reboot this device when convenient, then re-run: sudo ./scripts/cot-r prepare"
    cot_r_warn "Networking and SSH were not restarted by this script"
  else
    cot_r_ok "No reboot flagged by this run"
  fi
}

cot_r_cmd_reset_project() {
  require_root
  # Identity flags (e.g. --reserved-ipv4) are forwarded to the re-prepare step.
  cot_r_init_logging
  cot_r_info "Starting project reset (not an OS reinstall)"
  cot_r_ensure_state_dirs
  cot_r_snapshot_system "pre-reset-project" >/dev/null

  cot_r_stop_project_services

  # If bridged AP was enabled, tear it down so baseline Ethernet DHCP returns.
  # Keep AP PSK / ap.env (durable credentials) by default.
  if [[ -f "${COT_R_STATE_DIR}/ap-enabled" || -f /etc/systemd/network/00-cot-r-end0.network || -f /etc/netplan/60-cot-r-bridge.yaml ]]; then
    cot_r_info "AP was enabled; disabling bridge as part of project reset"
    systemctl stop "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true
    systemctl disable "${COT_R_HOSTAPD_UNIT}" 2>/dev/null || true
    rm -f /etc/systemd/network/00-cot-r-end0.network \
          /etc/systemd/network/00-cot-r-br0.network \
          /etc/systemd/network/00-cot-r-br0.netdev
    if [[ -f /etc/netplan/60-cot-r-bridge.yaml ]]; then
      backup_file /etc/netplan/60-cot-r-bridge.yaml >/dev/null
      rm -f /etc/netplan/60-cot-r-bridge.yaml
    fi
    netplan generate || true
    networkctl reload 2>/dev/null || systemctl try-reload-or-restart systemd-networkd || true
    ip link delete br0 type bridge 2>/dev/null || true
    rm -f "${COT_R_STATE_DIR}/ap-enabled"
  fi

  # Remove project-owned runtime payload (preserve /opt/cot-r/src deploy clone)
  if [[ -d "${COT_R_OPT_DIR}" ]]; then
    cot_r_info "Removing runtime payloads under ${COT_R_OPT_DIR} (keeping src/ if present)"
    find "${COT_R_OPT_DIR}" -mindepth 1 -maxdepth 1 ! -name src -exec rm -rf {} +
  fi

  # Remove generated display config (recreated by prepare); keep device.env secrets if any
  if [[ -f "${COT_R_ETC_DIR}/display.env" ]]; then
    backup_file "${COT_R_ETC_DIR}/display.env" >/dev/null
    rm -f "${COT_R_ETC_DIR}/display.env"
  fi

  # Remove disposable run state
  rm -rf "${COT_R_RUN_DIR}" 2>/dev/null || true

  # Disable unit files; reinstall via prepare
  local unit
  for unit in "${COT_R_MGMT_UNIT}" "${COT_R_DISPLAY_UNIT}"; do
    if [[ -f "/etc/systemd/system/${unit}" ]]; then
      systemctl disable "${unit}" 2>/dev/null || true
      backup_file "/etc/systemd/system/${unit}" >/dev/null
      rm -f "/etc/systemd/system/${unit}"
    fi
  done
  systemctl daemon-reload

  # Clear disposable management demo state; keep device.id, api.token, peer.token
  if [[ -f "${COT_R_STATE_DIR}/mgmt-state.json" ]]; then
    backup_file "${COT_R_STATE_DIR}/mgmt-state.json" >/dev/null
    rm -f "${COT_R_STATE_DIR}/mgmt-state.json"
  fi

  # Do NOT remove: admin users, SSH keys, ethernet config, development clones,
  # deploy clone, armbianEnv overlays, device.id, api.token, peer.token,
  # sudoers, baseline apt packages. Identity wipe is a separate future operation.

  cot_r_info "Re-applying baseline prepare"
  cot_r_cmd_prepare "$@"

  cot_r_ok "Project reset finished; baseline reapplied"
  cot_r_info "Preserved: OS, unrelated packages, admin users, SSH, Ethernet, git clones, identity/token, I2C overlay"
}
