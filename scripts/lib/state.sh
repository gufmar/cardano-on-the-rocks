#!/usr/bin/env bash
# Manifest and snapshot helpers.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

cot_r_ensure_state_dirs() {
  mkdir -p \
    "${COT_R_STATE_DIR}" \
    "${COT_R_BACKUP_DIR}" \
    "${COT_R_SNAPSHOT_DIR}" \
    "${COT_R_LOG_DIR}" \
    "${COT_R_ETC_DIR}" \
    "${COT_R_OPT_DIR}" \
    "${COT_R_RUN_DIR}"
  chmod 0750 "${COT_R_ETC_DIR}" || true
  chmod 0755 "${COT_R_STATE_DIR}" "${COT_R_OPT_DIR}" || true
}

cot_r_write_initial_manifest() {
  local ver ts
  ver="$(cot_r_version)"
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  if [[ -f "${COT_R_MANIFEST}" ]]; then
    return 0
  fi
  cat >"${COT_R_MANIFEST}" <<EOF
{
  "schema": 1,
  "project": "cardano-on-the-rocks",
  "version": "$(json_escape "$ver")",
  "created_at": "$(json_escape "$ts")",
  "updated_at": "$(json_escape "$ts")",
  "packages": [],
  "files": [],
  "services": [],
  "users": [],
  "overlays": [],
  "notes": []
}
EOF
  cot_r_info "Created manifest ${COT_R_MANIFEST}"
}

# Record a managed path. kind: file|dir|unit|udev|overlay
cot_r_manifest_add_file() {
  local path="$1"
  local kind="${2:-file}"
  local tmp
  require_cmd python3
  tmp="$(mktemp)"
  python3 - "$COT_R_MANIFEST" "$path" "$kind" "$tmp" <<'PY'
import json, sys, datetime
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
manifest, path, kind, out = sys.argv[1:5]
with open(manifest, encoding="utf-8") as f:
    data = json.load(f)
files = data.setdefault("files", [])
for entry in files:
    if entry.get("path") == path:
        entry["kind"] = kind
        entry["updated_at"] = now()
        break
else:
    files.append({"path": path, "kind": kind, "updated_at": now()})
data["updated_at"] = now()
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  mv -f "$tmp" "${COT_R_MANIFEST}"
}

cot_r_manifest_add_package() {
  local pkg="$1"
  local reason="${2:-baseline}"
  local tmp
  require_cmd python3
  tmp="$(mktemp)"
  python3 - "$COT_R_MANIFEST" "$pkg" "$reason" "$tmp" <<'PY'
import json, sys, datetime
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
manifest, pkg, reason, out = sys.argv[1:5]
with open(manifest, encoding="utf-8") as f:
    data = json.load(f)
pkgs = data.setdefault("packages", [])
for entry in pkgs:
    if entry.get("name") == pkg:
        entry["reason"] = reason
        entry["updated_at"] = now()
        break
else:
    pkgs.append({"name": pkg, "reason": reason, "updated_at": now()})
data["updated_at"] = now()
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  mv -f "$tmp" "${COT_R_MANIFEST}"
}

cot_r_manifest_add_service() {
  local unit="$1"
  local tmp
  require_cmd python3
  tmp="$(mktemp)"
  python3 - "$COT_R_MANIFEST" "$unit" "$tmp" <<'PY'
import json, sys, datetime
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
manifest, unit, out = sys.argv[1:4]
with open(manifest, encoding="utf-8") as f:
    data = json.load(f)
svcs = data.setdefault("services", [])
if unit not in svcs:
    svcs.append(unit)
data["updated_at"] = now()
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  mv -f "$tmp" "${COT_R_MANIFEST}"
}

cot_r_manifest_add_user() {
  local user="$1"
  local tmp
  require_cmd python3
  tmp="$(mktemp)"
  python3 - "$COT_R_MANIFEST" "$user" "$tmp" <<'PY'
import json, sys, datetime
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
manifest, user, out = sys.argv[1:4]
with open(manifest, encoding="utf-8") as f:
    data = json.load(f)
users = data.setdefault("users", [])
if user not in users:
    users.append(user)
data["updated_at"] = now()
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  mv -f "$tmp" "${COT_R_MANIFEST}"
}

cot_r_manifest_add_overlay() {
  local overlay="$1"
  local tmp
  require_cmd python3
  tmp="$(mktemp)"
  python3 - "$COT_R_MANIFEST" "$overlay" "$tmp" <<'PY'
import json, sys, datetime
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
manifest, overlay, out = sys.argv[1:4]
with open(manifest, encoding="utf-8") as f:
    data = json.load(f)
ovs = data.setdefault("overlays", [])
if overlay not in ovs:
    ovs.append(overlay)
data["updated_at"] = now()
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  mv -f "$tmp" "${COT_R_MANIFEST}"
}

cot_r_snapshot_system() {
  local label="${1:-pre-prepare}"
  local dest
  dest="${COT_R_SNAPSHOT_DIR}/$(date -u '+%Y%m%dT%H%M%SZ')-${label}.txt"
  mkdir -p "${COT_R_SNAPSHOT_DIR}"
  {
    echo "# cot-r snapshot label=${label}"
    echo "timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "hostname=$(hostname 2>/dev/null || true)"
    echo "uname=$(uname -a 2>/dev/null || true)"
    echo "--- /etc/os-release ---"
    cat /etc/os-release 2>/dev/null || true
    echo "--- architecture ---"
    dpkg --print-architecture 2>/dev/null || uname -m
    echo "--- armbianEnv ---"
    cat "${COT_R_ARMBIAN_ENV}" 2>/dev/null || true
    echo "--- ip addr ---"
    ip -br addr 2>/dev/null || true
    echo "--- ip route ---"
    ip route 2>/dev/null || true
    echo "--- i2c devices ---"
    ls -la /dev/i2c-* 2>/dev/null || true
    echo "--- systemd network ---"
    systemctl is-active systemd-networkd 2>/dev/null || true
    systemctl is-active NetworkManager 2>/dev/null || true
    echo "--- listening ssh ---"
    ss -tlnp 2>/dev/null | grep -E ':22\s' || true
    echo "--- users >=1000 ---"
    getent passwd | awk -F: '$3>=1000 {print}' || true
  } >"$dest"
  cot_r_info "Wrote snapshot $dest"
  printf '%s\n' "$dest"
}
