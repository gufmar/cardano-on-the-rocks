#!/usr/bin/env bash
# prepare / reset-project orchestration.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

cot_r_cmd_prepare() {
  local reboot_line reboot_needed=no
  require_root
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

  # 3) Users / dirs / udev
  cot_r_ensure_display_user
  cot_r_install_udev_rules
  cot_r_install_config_templates

  # 4) I2C7 overlay (no reboot performed)
  reboot_line="$(cot_r_ensure_i2c7_overlay | tee /dev/stderr | grep '^reboot_needed=' | tail -n1 || true)"
  if [[ "$reboot_line" == "reboot_needed=yes" ]]; then
    reboot_needed=yes
  fi

  # 5) Display runtime + service
  cot_r_install_display_code
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

  cot_r_snapshot_system "post-prepare" >/dev/null
  echo
  cot_r_ok "Prepare completed"
  echo "Completed steps: snapshot, packages, user/udev, overlay staging, display install"
  if [[ "$reboot_needed" == "yes" ]]; then
    cot_r_warn "ACTION REQUIRED: reboot this device when convenient, then re-run: sudo ./scripts/cot-r prepare"
    cot_r_warn "Networking and SSH were not restarted by this script"
  else
    cot_r_ok "No reboot flagged by this run"
  fi
}

cot_r_cmd_reset_project() {
  require_root
  cot_r_init_logging
  cot_r_info "Starting project reset (not an OS reinstall)"
  cot_r_ensure_state_dirs
  cot_r_snapshot_system "pre-reset-project" >/dev/null

  cot_r_stop_project_services

  # Remove project-owned runtime payload
  if [[ -d "${COT_R_OPT_DIR}" ]]; then
    cot_r_info "Removing ${COT_R_OPT_DIR}"
    rm -rf "${COT_R_OPT_DIR}"
  fi

  # Remove generated display config (recreated by prepare); keep device.env secrets if any
  if [[ -f "${COT_R_ETC_DIR}/display.env" ]]; then
    backup_file "${COT_R_ETC_DIR}/display.env" >/dev/null
    rm -f "${COT_R_ETC_DIR}/display.env"
  fi

  # Remove disposable run state
  rm -rf "${COT_R_RUN_DIR}" 2>/dev/null || true

  # Disable unit file but keep backed-up copy in backups via prepare reinstall
  if [[ -f "/etc/systemd/system/${COT_R_DISPLAY_UNIT}" ]]; then
    systemctl disable "${COT_R_DISPLAY_UNIT}" 2>/dev/null || true
    backup_file "/etc/systemd/system/${COT_R_DISPLAY_UNIT}" >/dev/null
    rm -f "/etc/systemd/system/${COT_R_DISPLAY_UNIT}"
    systemctl daemon-reload
  fi

  # Do NOT remove: admin users, SSH keys, ethernet config, development clones,
  # armbianEnv overlays (re-prepare is idempotent), baseline apt packages that
  # may be used elsewhere. Identity wipe is a separate future operation.

  cot_r_info "Re-applying baseline prepare"
  cot_r_cmd_prepare

  cot_r_ok "Project reset finished; baseline reapplied"
  cot_r_info "Preserved: OS, unrelated packages, admin users, SSH, Ethernet, git clones, I2C overlay entry"
}
