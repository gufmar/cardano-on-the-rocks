#!/usr/bin/env bash
# Install display runtime and systemd unit.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

cot_r_install_display_code() {
  local dest="${COT_R_OPT_DIR}/display"
  mkdir -p "${COT_R_OPT_DIR}"
  rm -rf "${dest}.new"
  mkdir -p "${dest}.new"
  cp -a "${COT_R_DISPLAY_SRC}/." "${dest}.new/"
  if [[ -d "$dest" ]]; then
    rm -rf "${dest}.old" 2>/dev/null || true
    mv "$dest" "${dest}.old"
  fi
  mv "${dest}.new" "$dest"
  rm -rf "${dest}.old" 2>/dev/null || true
  chown -R "root:${COT_R_DISPLAY_USER}" "$dest" 2>/dev/null || true
  chmod -R a+rX "$dest"
  cot_r_manifest_add_file "$dest" "dir"
  cot_r_ok "Installed display code to ${dest}"
}

cot_r_install_display_unit() {
  local src dest
  src="${COT_R_DEPLOY_DIR}/systemd/${COT_R_DISPLAY_UNIT}"
  dest="/etc/systemd/system/${COT_R_DISPLAY_UNIT}"
  [[ -f "$src" ]] || die "Missing unit template: ${src}"
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    cot_r_info "systemd unit already up to date"
  else
    if [[ -f "$dest" ]]; then
      backup_file "$dest" >/dev/null
    fi
    install -m 0644 "$src" "$dest"
    cot_r_ok "Installed ${dest}"
  fi
  systemctl daemon-reload
  systemctl enable "${COT_R_DISPLAY_UNIT}"
  # Start only if OLED config exists OR allow degraded start (unit handles missing hw).
  if systemctl is-active --quiet "${COT_R_DISPLAY_UNIT}"; then
    systemctl reload-or-restart "${COT_R_DISPLAY_UNIT}" || \
      systemctl restart "${COT_R_DISPLAY_UNIT}" || \
      cot_r_warn "Could not restart ${COT_R_DISPLAY_UNIT}"
  else
    systemctl start "${COT_R_DISPLAY_UNIT}" || \
      cot_r_warn "Display service failed to start (OLED may be absent until reboot); networking unaffected"
  fi
  cot_r_manifest_add_service "${COT_R_DISPLAY_UNIT}"
  cot_r_manifest_add_file "$dest" "unit"
}

cot_r_install_config_templates() {
  mkdir -p "${COT_R_ETC_DIR}"
  local f
  for f in device.env.example display.env.example; do
    if [[ -f "${COT_R_CONFIG_TEMPLATES}/${f}" ]]; then
      install -m 0644 "${COT_R_CONFIG_TEMPLATES}/${f}" "${COT_R_ETC_DIR}/${f}"
      cot_r_manifest_add_file "${COT_R_ETC_DIR}/${f}" "config-template"
    fi
  done
  # Seed device.env from example if missing (no secrets).
  if [[ ! -f "${COT_R_ETC_DIR}/device.env" && -f "${COT_R_ETC_DIR}/device.env.example" ]]; then
    install -m 0640 "${COT_R_ETC_DIR}/device.env.example" "${COT_R_ETC_DIR}/device.env"
    cot_r_manifest_add_file "${COT_R_ETC_DIR}/device.env" "config"
  fi
}

cot_r_stop_project_services() {
  local unit
  for unit in "${COT_R_DISPLAY_UNIT}"; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
      systemctl stop "$unit" 2>/dev/null || true
      cot_r_info "Stopped ${unit}"
    fi
  done
}
