#!/usr/bin/env bash
# Deployment clone, update, and rollback.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

# Deploy clone is separate from the development checkout on RockPi 2.
COT_R_DEPLOY_USER="${COT_R_DEPLOY_USER:-cot-r}"
COT_R_DEPLOY_HOME="${COT_R_DEPLOY_HOME:-/var/lib/cot-r/home}"
COT_R_DEPLOY_CLONE="${COT_R_DEPLOY_CLONE:-/opt/cot-r/src}"
COT_R_DEPLOY_INFO="${COT_R_STATE_DIR}/deploy.json"
COT_R_DEFAULT_REMOTE_URL="${COT_R_DEFAULT_REMOTE_URL:-https://github.com/gufmar/cardano-on-the-rocks.git}"

cot_r_ensure_deploy_user() {
  if getent passwd "${COT_R_DEPLOY_USER}" >/dev/null 2>&1; then
    cot_r_info "User ${COT_R_DEPLOY_USER} exists"
  else
    useradd --system --create-home --home-dir "${COT_R_DEPLOY_HOME}" \
      --shell /usr/sbin/nologin "${COT_R_DEPLOY_USER}"
    cot_r_ok "Created user ${COT_R_DEPLOY_USER}"
  fi
  mkdir -p "${COT_R_DEPLOY_HOME}"
  chown "${COT_R_DEPLOY_USER}:${COT_R_DEPLOY_USER}" "${COT_R_DEPLOY_HOME}"
  # Display socket access when group perms allow
  if getent group "${COT_R_I2C_GROUP}" >/dev/null 2>&1; then
    usermod -a -G "${COT_R_I2C_GROUP}" "${COT_R_DEPLOY_USER}" 2>/dev/null || true
  fi
  cot_r_manifest_add_user "${COT_R_DEPLOY_USER}"
}

cot_r_repo_url() {
  local url=""
  if [[ -d "${COT_R_REPO_ROOT}/.git" ]]; then
    url="$(git -C "${COT_R_REPO_ROOT}" remote get-url origin 2>/dev/null || true)"
  fi
  if [[ -z "$url" ]]; then
    url="${COT_R_DEFAULT_REMOTE_URL}"
  fi
  printf '%s\n' "$url"
}

cot_r_ensure_deploy_clone() {
  local url
  url="$(cot_r_repo_url)"
  mkdir -p "$(dirname "${COT_R_DEPLOY_CLONE}")"
  if [[ -d "${COT_R_DEPLOY_CLONE}/.git" ]]; then
    cot_r_info "Deploy clone present: ${COT_R_DEPLOY_CLONE}"
  else
    cot_r_info "Creating deploy tree at ${COT_R_DEPLOY_CLONE} (remote ${url})"
    # Local clone needs at least one commit. Until then, seed from the working tree.
    if [[ -d "${COT_R_REPO_ROOT}/.git" ]] && git -C "${COT_R_REPO_ROOT}" rev-parse HEAD >/dev/null 2>&1; then
      git clone -- "${COT_R_REPO_ROOT}" "${COT_R_DEPLOY_CLONE}"
      git -C "${COT_R_DEPLOY_CLONE}" remote set-url origin "$url" 2>/dev/null || true
    elif git ls-remote "$url" HEAD >/dev/null 2>&1; then
      git clone -- "$url" "${COT_R_DEPLOY_CLONE}"
    else
      cot_r_warn "No git commit/remote available yet; seeding deploy tree from working copy"
      mkdir -p "${COT_R_DEPLOY_CLONE}"
      # Copy tracked-style project content without .git
      rsync -a --delete \
        --exclude '.git' \
        --exclude '.venv' \
        --exclude '__pycache__' \
        --exclude 'state' \
        "${COT_R_REPO_ROOT}/" "${COT_R_DEPLOY_CLONE}/"
      git -C "${COT_R_DEPLOY_CLONE}" init -b main >/dev/null
      git -C "${COT_R_DEPLOY_CLONE}" remote add origin "$url" 2>/dev/null || \
        git -C "${COT_R_DEPLOY_CLONE}" remote set-url origin "$url"
      # Leave uncommitted until the operator makes the first signed commit upstream.
    fi
    cot_r_ok "Created deploy clone/tree"
  fi
  chown -R "${COT_R_DEPLOY_USER}:${COT_R_DEPLOY_USER}" "${COT_R_DEPLOY_CLONE}"
  cot_r_manifest_add_file "${COT_R_DEPLOY_CLONE}" "dir"
}

cot_r_git_head() {
  local repo="$1"
  git -C "$repo" rev-parse HEAD 2>/dev/null || true
}

cot_r_write_deploy_info() {
  local commit="$1"
  local ref="$2"
  local previous="${3:-}"
  local tmp ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  tmp="$(mktemp)"
  cat >"$tmp" <<EOF
{
  "schema": 1,
  "clone": "$(json_escape "${COT_R_DEPLOY_CLONE}")",
  "remote": "$(json_escape "$(cot_r_repo_url)")",
  "commit": "$(json_escape "$commit")",
  "ref": "$(json_escape "$ref")",
  "previous_commit": "$(json_escape "$previous")",
  "deployed_at": "$(json_escape "$ts")",
  "version_file": "$(json_escape "$(cot_r_version)")"
}
EOF
  mkdir -p "${COT_R_STATE_DIR}"
  mv -f "$tmp" "${COT_R_DEPLOY_INFO}"
  chmod 0640 "${COT_R_DEPLOY_INFO}"
  chown "root:${COT_R_DEPLOY_USER}" "${COT_R_DEPLOY_INFO}" 2>/dev/null || true
  cot_r_manifest_add_file "${COT_R_DEPLOY_INFO}" "state"
}

cot_r_install_from_tree() {
  # Install runtime payloads from a source tree (repo root or deploy clone).
  local tree="$1"
  COT_R_DISPLAY_SRC="${tree}/display"
  COT_R_MGMT_SRC="${tree}/mgmt"
  [[ -d "${COT_R_DISPLAY_SRC}" ]] || die "Missing display/ in ${tree}"
  [[ -d "${COT_R_MGMT_SRC}" ]] || die "Missing mgmt/ in ${tree}"
  cot_r_install_display_code
  cot_r_install_mgmt_code
}

cot_r_cmd_deploy_status() {
  echo "=== Deploy ==="
  echo "user:   ${COT_R_DEPLOY_USER}"
  echo "clone:  ${COT_R_DEPLOY_CLONE}"
  echo "remote: $(cot_r_repo_url)"
  if [[ -d "${COT_R_DEPLOY_CLONE}/.git" ]]; then
    echo "HEAD:   $(cot_r_git_head "${COT_R_DEPLOY_CLONE}")"
    git -C "${COT_R_DEPLOY_CLONE}" status -sb 2>/dev/null | sed 's/^/  /' || true
  else
    echo "clone:  (missing)"
  fi
  if [[ -f "${COT_R_DEPLOY_INFO}" ]]; then
    echo "deploy.json:"
    if command -v jq >/dev/null 2>&1; then
      jq . "${COT_R_DEPLOY_INFO}"
    else
      cat "${COT_R_DEPLOY_INFO}"
    fi
  else
    echo "deploy.json: (missing)"
  fi
}

cot_r_cmd_update() {
  require_root
  local ref="${1:-}"
  local force=0
  if [[ "${1:-}" == "--force" ]]; then
    force=1
    ref="${2:-}"
  elif [[ "${2:-}" == "--force" ]]; then
    force=1
  fi
  cot_r_init_logging
  cot_r_ensure_deploy_user
  cot_r_ensure_deploy_clone

  local clone="${COT_R_DEPLOY_CLONE}"
  local prev dirty
  prev="$(cot_r_git_head "$clone")"
  dirty="$(git -C "$clone" status --porcelain 2>/dev/null || true)"
  if [[ -n "$dirty" && "$force" -ne 1 ]]; then
    cot_r_error "Deploy clone has local modifications; refusing to update"
    echo "$dirty" | sed 's/^/  /'
    die "Re-run with: sudo ./scripts/cot-r update --force [ref]  (discards local changes in ${clone} only)"
  fi
  if [[ "$force" -eq 1 && -n "$dirty" ]]; then
    cot_r_warn "Discarding local modifications in deploy clone (--force)"
    git -C "$clone" reset --hard HEAD
    git -C "$clone" clean -fd
  fi

  cot_r_info "Fetching origin in ${clone}"
  git -C "$clone" fetch --tags --prune origin

  if [[ -n "$ref" && "$ref" != "--force" ]]; then
    cot_r_info "Checking out ${ref}"
    git -C "$clone" checkout --force "$ref"
  else
    # Track current branch or fall back to main/master
    local branch
    branch="$(git -C "$clone" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    if [[ "$branch" == "HEAD" ]]; then
      branch="main"
    fi
    if git -C "$clone" show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
      git -C "$clone" checkout -B "$branch" "origin/${branch}"
    elif git -C "$clone" show-ref --verify --quiet refs/remotes/origin/main; then
      git -C "$clone" checkout -B main origin/main
    else
      die "Could not resolve update ref; pass an explicit commit or tag"
    fi
    ref="$branch"
  fi

  local new
  new="$(cot_r_git_head "$clone")"
  [[ -n "$new" ]] || die "Could not resolve HEAD after update"

  # Temporarily point install helpers at deploy tree version file if present
  if [[ -f "${clone}/VERSION" ]]; then
    COT_R_VERSION_FILE="${clone}/VERSION"
  fi
  cot_r_install_from_tree "$clone"
  cot_r_install_display_unit
  cot_r_install_mgmt_unit
  cot_r_write_deploy_info "$new" "$ref" "$prev"

  cot_r_ok "Updated to ${new} (ref=${ref})"
  if [[ -n "$prev" && "$prev" != "$new" ]]; then
    cot_r_info "Previous commit recorded for rollback: ${prev}"
  fi
}

cot_r_cmd_rollback() {
  require_root
  cot_r_init_logging
  [[ -f "${COT_R_DEPLOY_INFO}" ]] || die "No ${COT_R_DEPLOY_INFO}; nothing to roll back to"
  local prev
  prev="$(python3 - "${COT_R_DEPLOY_INFO}" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("previous_commit") or "")
PY
)"
  [[ -n "$prev" ]] || die "deploy.json has no previous_commit"
  cot_r_info "Rolling back deploy clone to ${prev}"
  cot_r_cmd_update "$prev"
}
