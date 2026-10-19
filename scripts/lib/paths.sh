#!/usr/bin/env bash
# Runtime and repository path constants.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

COT_R_REPO_ROOT="$(cd "${COT_R_LIB_DIR}/../.." && pwd)"
COT_R_VERSION_FILE="${COT_R_REPO_ROOT}/VERSION"
COT_R_SCRIPTS_DIR="${COT_R_REPO_ROOT}/scripts"
COT_R_DISPLAY_SRC="${COT_R_REPO_ROOT}/display"
COT_R_MGMT_SRC="${COT_R_REPO_ROOT}/mgmt"
COT_R_DEPLOY_DIR="${COT_R_REPO_ROOT}/deploy"
COT_R_CONFIG_TEMPLATES="${COT_R_REPO_ROOT}/config/templates"
COT_R_UDEV_SRC="${COT_R_REPO_ROOT}/config/udev"

# System locations (project-owned)
COT_R_ETC_DIR="${COT_R_ETC_DIR:-/etc/cot-r}"
COT_R_STATE_DIR="${COT_R_STATE_DIR:-/var/lib/cot-r}"
COT_R_BACKUP_DIR="${COT_R_STATE_DIR}/backups"
COT_R_LOG_DIR="${COT_R_LOG_DIR:-/var/log/cot-r}"
COT_R_OPT_DIR="${COT_R_OPT_DIR:-/opt/cot-r}"
COT_R_RUN_DIR="${COT_R_RUN_DIR:-/run/cot-r}"

COT_R_MANIFEST="${COT_R_STATE_DIR}/manifest.json"
COT_R_SNAPSHOT_DIR="${COT_R_STATE_DIR}/snapshots"
COT_R_DISPLAY_USER="${COT_R_DISPLAY_USER:-cot-r-display}"
COT_R_DEPLOY_USER="${COT_R_DEPLOY_USER:-cot-r}"
COT_R_I2C_GROUP="${COT_R_I2C_GROUP:-i2c}"
COT_R_OVERLAY_I2C7="rk3399-i2c7"
COT_R_ARMBIAN_ENV="/boot/armbianEnv.txt"
COT_R_DISPLAY_UNIT="cot-r-display.service"
COT_R_MGMT_UNIT="cot-r-mgmt.service"
COT_R_UDEV_RULE="99-cot-r-i2c.rules"
COT_R_DISPLAY_SOCK="${COT_R_RUN_DIR}/display.sock"
COT_R_API_TOKEN_FILE="${COT_R_ETC_DIR}/api.token"
COT_R_DEPLOY_CLONE="${COT_R_DEPLOY_CLONE:-/opt/cot-r/src}"
COT_R_DEPLOY_INFO="${COT_R_STATE_DIR}/deploy.json"

cot_r_version() {
  if [[ -f "${COT_R_VERSION_FILE}" ]]; then
    tr -d '[:space:]' <"${COT_R_VERSION_FILE}"
  else
    printf 'unknown'
  fi
}
