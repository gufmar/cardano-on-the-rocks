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

cot_r_fix_etc_permissions() {
  # Management runs as user cot-r and must read configs/token under /etc/cot-r.
  mkdir -p "${COT_R_ETC_DIR}"
  chown "root:${COT_R_DEPLOY_USER}" "${COT_R_ETC_DIR}"
  chmod 0750 "${COT_R_ETC_DIR}"
  local f
  for f in device.env mgmt.env display.env device.id api.token \
           device.env.example mgmt.env.example display.env.example; do
    if [[ -e "${COT_R_ETC_DIR}/${f}" ]]; then
      chown "root:${COT_R_DEPLOY_USER}" "${COT_R_ETC_DIR}/${f}"
      case "$f" in
        *.example) chmod 0644 "${COT_R_ETC_DIR}/${f}" ;;
        api.token|device.id|device.env|mgmt.env|display.env) chmod 0640 "${COT_R_ETC_DIR}/${f}" ;;
        *) chmod 0640 "${COT_R_ETC_DIR}/${f}" ;;
      esac
    fi
  done
  cot_r_ok "Fixed /etc/cot-r ownership for group ${COT_R_DEPLOY_USER}"
}

cot_r_install_config_templates() {
  mkdir -p "${COT_R_ETC_DIR}"
  local f
  for f in device.env.example display.env.example mgmt.env.example; do
    if [[ -f "${COT_R_CONFIG_TEMPLATES}/${f}" ]]; then
      install -m 0644 -o root -g "${COT_R_DEPLOY_USER}" \
        "${COT_R_CONFIG_TEMPLATES}/${f}" "${COT_R_ETC_DIR}/${f}"
      cot_r_manifest_add_file "${COT_R_ETC_DIR}/${f}" "config-template"
    fi
  done
  # Seed device.env from example if missing (no secrets).
  if [[ ! -f "${COT_R_ETC_DIR}/device.env" && -f "${COT_R_ETC_DIR}/device.env.example" ]]; then
    install -m 0640 -o root -g "${COT_R_DEPLOY_USER}" \
      "${COT_R_ETC_DIR}/device.env.example" "${COT_R_ETC_DIR}/device.env"
    cot_r_manifest_add_file "${COT_R_ETC_DIR}/device.env" "config"
  fi
  if [[ ! -f "${COT_R_ETC_DIR}/mgmt.env" && -f "${COT_R_ETC_DIR}/mgmt.env.example" ]]; then
    install -m 0640 -o root -g "${COT_R_DEPLOY_USER}" \
      "${COT_R_ETC_DIR}/mgmt.env.example" "${COT_R_ETC_DIR}/mgmt.env"
    cot_r_manifest_add_file "${COT_R_ETC_DIR}/mgmt.env" "config"
  fi
  cot_r_fix_etc_permissions
}

cot_r_ensure_api_token() {
  mkdir -p "${COT_R_ETC_DIR}"
  if [[ -s "${COT_R_API_TOKEN_FILE}" ]]; then
    cot_r_info "API token already present"
  else
    python3 - "${COT_R_API_TOKEN_FILE}" <<'PY'
import secrets, sys
from pathlib import Path
path = Path(sys.argv[1])
path.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
path.chmod(0o640)
PY
    cot_r_ok "Created ${COT_R_API_TOKEN_FILE}"
  fi
  chown "root:${COT_R_DEPLOY_USER}" "${COT_R_API_TOKEN_FILE}" 2>/dev/null || \
    chown "root:root" "${COT_R_API_TOKEN_FILE}"
  chmod 0640 "${COT_R_API_TOKEN_FILE}"
  cot_r_manifest_add_file "${COT_R_API_TOKEN_FILE}" "secret"
}

cot_r_ensure_device_id_file() {
  local idf="${COT_R_ETC_DIR}/device.id"
  mkdir -p "${COT_R_ETC_DIR}"
  if [[ -s "$idf" ]]; then
    cot_r_info "device.id present"
  else
    python3 - "$idf" <<'PY'
import uuid, sys
from pathlib import Path
Path(sys.argv[1]).write_text(str(uuid.uuid4()) + "\n", encoding="utf-8")
PY
    cot_r_ok "Created ${idf}"
  fi
  chmod 0640 "$idf"
  chown "root:${COT_R_DEPLOY_USER}" "$idf" 2>/dev/null || true
  cot_r_manifest_add_file "$idf" "identity"
}

cot_r_install_mgmt_code() {
  local dest="${COT_R_OPT_DIR}/mgmt"
  local src="${COT_R_MGMT_SRC}"
  [[ -d "$src" ]] || die "Missing mgmt source: ${src}"
  mkdir -p "${COT_R_OPT_DIR}"
  rm -rf "${dest}.new"
  mkdir -p "${dest}.new"
  cp -a "${src}/." "${dest}.new/"
  if [[ -d "$dest" ]]; then
    rm -rf "${dest}.old" 2>/dev/null || true
    mv "$dest" "${dest}.old"
  fi
  mv "${dest}.new" "$dest"
  rm -rf "${dest}.old" 2>/dev/null || true
  chown -R "root:${COT_R_DEPLOY_USER}" "$dest" 2>/dev/null || true
  chmod -R a+rX "$dest"
  cot_r_manifest_add_file "$dest" "dir"
  cot_r_ok "Installed management code to ${dest}"
}

cot_r_install_mgmt_unit() {
  local src dest
  src="${COT_R_DEPLOY_DIR}/systemd/${COT_R_MGMT_UNIT}"
  dest="/etc/systemd/system/${COT_R_MGMT_UNIT}"
  [[ -f "$src" ]] || die "Missing unit template: ${src}"
  # Ensure state dirs writable by cot-r for mgmt-state.json
  mkdir -p "${COT_R_STATE_DIR}" "${COT_R_RUN_DIR}" "${COT_R_LOG_DIR}"
  chown "root:${COT_R_DEPLOY_USER}" "${COT_R_STATE_DIR}" 2>/dev/null || true
  chmod 2775 "${COT_R_STATE_DIR}" || true
  if [[ -f "${COT_R_STATE_DIR}/mgmt-state.json" ]]; then
    chown "${COT_R_DEPLOY_USER}:${COT_R_DEPLOY_USER}" "${COT_R_STATE_DIR}/mgmt-state.json" 2>/dev/null || true
    chmod 0660 "${COT_R_STATE_DIR}/mgmt-state.json" 2>/dev/null || true
  fi

  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    cot_r_info "mgmt systemd unit already up to date"
  else
    if [[ -f "$dest" ]]; then
      backup_file "$dest" >/dev/null
    fi
    install -m 0644 "$src" "$dest"
    cot_r_ok "Installed ${dest}"
  fi
  systemctl daemon-reload
  systemctl enable "${COT_R_MGMT_UNIT}"
  if systemctl is-active --quiet "${COT_R_MGMT_UNIT}"; then
    systemctl restart "${COT_R_MGMT_UNIT}" || cot_r_warn "Could not restart ${COT_R_MGMT_UNIT}"
  else
    systemctl start "${COT_R_MGMT_UNIT}" || cot_r_warn "Management service failed to start"
  fi
  cot_r_manifest_add_service "${COT_R_MGMT_UNIT}"
  cot_r_manifest_add_file "$dest" "unit"
}

cot_r_stop_project_services() {
  local unit
  for unit in "${COT_R_MGMT_UNIT}" "${COT_R_DISPLAY_UNIT}"; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
      systemctl stop "$unit" 2>/dev/null || true
      cot_r_info "Stopped ${unit}"
    fi
  done
}
