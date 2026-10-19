#!/usr/bin/env bash
# Package installation with manifest tracking.
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

# Baseline admin / diagnostic tools requested by the owner.
COT_R_BASE_PACKAGES=(
  htop
  pydf
  mtr
  net-tools
  inetutils-traceroute
  curl
  jq
  git
)

# OLED / display foundation (Debian trixie names verified on this host).
COT_R_DISPLAY_PACKAGES=(
  i2c-tools
  python3-luma.oled
  python3-luma.core
  python3-pil
  python3-smbus2
  python3-qrcode
)

cot_r_apt_update() {
  cot_r_info "Refreshing apt package metadata"
  DEBIAN_FRONTEND=noninteractive apt-get update -y
}

cot_r_pkg_available() {
  local pkg="$1"
  apt-cache show "$pkg" >/dev/null 2>&1
}

cot_r_pkg_installed() {
  local pkg="$1"
  dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'
}

cot_r_install_packages() {
  local reason="$1"
  shift
  local pkg
  local to_install=()
  for pkg in "$@"; do
    if ! cot_r_pkg_available "$pkg"; then
      die "Package not available on this distribution: ${pkg} (document a substitution if names differ)"
    fi
    if cot_r_pkg_installed "$pkg"; then
      cot_r_info "Already installed: ${pkg}"
      cot_r_manifest_add_package "$pkg" "$reason"
      continue
    fi
    to_install+=("$pkg")
  done
  if [[ "${#to_install[@]}" -eq 0 ]]; then
    cot_r_ok "No new packages to install (${reason})"
    return 0
  fi
  cot_r_info "Installing (${reason}): ${to_install[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${to_install[@]}"
  for pkg in "${to_install[@]}"; do
    cot_r_manifest_add_package "$pkg" "$reason"
  done
  cot_r_ok "Installed: ${to_install[*]}"
}

cot_r_install_baseline_packages() {
  cot_r_apt_update
  cot_r_install_packages "baseline" "${COT_R_BASE_PACKAGES[@]}"
}

cot_r_install_display_packages() {
  cot_r_install_packages "display" "${COT_R_DISPLAY_PACKAGES[@]}"
}
