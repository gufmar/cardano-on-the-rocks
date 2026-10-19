#!/usr/bin/env bash
# Project users, groups, and directories.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

cot_r_ensure_i2c_group() {
  if getent group "${COT_R_I2C_GROUP}" >/dev/null 2>&1; then
    cot_r_info "Group ${COT_R_I2C_GROUP} exists"
  else
    groupadd --system "${COT_R_I2C_GROUP}"
    cot_r_ok "Created group ${COT_R_I2C_GROUP}"
  fi
}

cot_r_ensure_display_user() {
  cot_r_ensure_i2c_group
  if getent passwd "${COT_R_DISPLAY_USER}" >/dev/null 2>&1; then
    cot_r_info "User ${COT_R_DISPLAY_USER} exists"
  else
    useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin \
      --gid "${COT_R_I2C_GROUP}" --groups "${COT_R_I2C_GROUP}" \
      "${COT_R_DISPLAY_USER}"
    cot_r_ok "Created user ${COT_R_DISPLAY_USER}"
  fi
  # Ensure membership even if user pre-existed without the group.
  usermod -a -G "${COT_R_I2C_GROUP}" "${COT_R_DISPLAY_USER}" 2>/dev/null || true
  cot_r_manifest_add_user "${COT_R_DISPLAY_USER}"
}

cot_r_install_udev_rules() {
  local src dest
  src="${COT_R_UDEV_SRC}/${COT_R_UDEV_RULE}"
  dest="/etc/udev/rules.d/${COT_R_UDEV_RULE}"
  [[ -f "$src" ]] || die "Missing udev rules template: ${src}"
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    cot_r_info "udev rule already up to date: ${dest}"
  else
    if [[ -f "$dest" ]]; then
      backup_file "$dest" >/dev/null
    fi
    install -m 0644 "$src" "$dest"
    cot_r_ok "Installed ${dest}"
    if command -v udevadm >/dev/null 2>&1; then
      udevadm control --reload-rules || true
      udevadm trigger --subsystem-match=i2c-dev || true
    fi
  fi
  cot_r_manifest_add_file "$dest" "udev"
}
