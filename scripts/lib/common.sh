#!/usr/bin/env bash
# Shared helpers for cot-r. Sourced only; not executed directly.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

set -euo pipefail

COT_R_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=paths.sh
source "${COT_R_LIB_DIR}/paths.sh"

COT_R_RED=$'\033[31m'
COT_R_GRN=$'\033[32m'
COT_R_YLW=$'\033[33m'
COT_R_BLU=$'\033[34m'
COT_R_RST=$'\033[0m'

cot_r_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '%s [%s] %s\n' "$ts" "$level" "$*" >&2
  if [[ -n "${COT_R_LOG_FILE:-}" && -w "${COT_R_LOG_FILE:-}" ]] || [[ -n "${COT_R_LOG_FILE:-}" && -d "$(dirname "${COT_R_LOG_FILE}")" ]]; then
    printf '%s [%s] %s\n' "$ts" "$level" "$*" >>"${COT_R_LOG_FILE}" 2>/dev/null || true
  fi
}

cot_r_info()  { cot_r_log INFO "$*"; }
cot_r_warn()  { cot_r_log WARN "${COT_R_YLW}$*${COT_R_RST}"; }
cot_r_error() { cot_r_log ERROR "${COT_R_RED}$*${COT_R_RST}"; }
cot_r_ok()    { cot_r_log OK "${COT_R_GRN}$*${COT_R_RST}"; }

die() {
  cot_r_error "$*"
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "This command requires root (use sudo). Refusing to continue."
  fi
}

require_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "Required command not found: $c"
  done
}

# Append a line to a file only if an exact match is absent (idempotent).
ensure_line_in_file() {
  local file="$1"
  local line="$2"
  if [[ -f "$file" ]] && grep -qxF -- "$line" "$file"; then
    return 0
  fi
  printf '%s\n' "$line" >>"$file"
}

backup_file() {
  local src="$1"
  local dest_dir="${COT_R_BACKUP_DIR}"
  local base ts dest
  [[ -e "$src" ]] || return 0
  mkdir -p "$dest_dir"
  base="$(basename "$src" | tr '/' '_')"
  ts="$(date -u '+%Y%m%dT%H%M%SZ')"
  dest="${dest_dir}/${base}.${ts}.bak"
  cp -a -- "$src" "$dest"
  cot_r_info "Backed up $src -> $dest"
  printf '%s\n' "$dest"
}

json_escape() {
  # Minimal JSON string escape for simple ASCII/UTF-8 text without control chars.
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

cot_r_init_logging() {
  mkdir -p "${COT_R_LOG_DIR}" 2>/dev/null || true
  if [[ -w "${COT_R_LOG_DIR}" ]] || [[ "${EUID}" -eq 0 ]]; then
    mkdir -p "${COT_R_LOG_DIR}"
    COT_R_LOG_FILE="${COT_R_LOG_DIR}/cot-r.log"
    touch "${COT_R_LOG_FILE}" 2>/dev/null || true
  fi
}
