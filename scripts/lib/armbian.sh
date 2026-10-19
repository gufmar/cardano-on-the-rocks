#!/usr/bin/env bash
# Armbian boot overlay helpers (RockPi 4B / RK3399).
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only

# Returns 0 if overlay name is already present in overlays= line.
cot_r_overlay_listed() {
  local name="$1"
  local env="${COT_R_ARMBIAN_ENV}"
  [[ -f "$env" ]] || return 1
  # Match overlays=... token among space-separated names.
  awk -F= -v n="$name" '
    $1 == "overlays" {
      split($2, a, /[ \t]+/)
      for (i in a) if (a[i] == n) found=1
    }
    END { exit found ? 0 : 1 }
  ' "$env"
}

# Idempotently add an overlay to armbianEnv.txt without duplicating.
# Prints "reboot_needed=yes|no" on stdout as a machine-readable hint.
cot_r_ensure_overlay() {
  local name="$1"
  local env="${COT_R_ARMBIAN_ENV}"
  local reboot_needed=no
  local prefix overlay_file

  [[ -f "$env" ]] || die "Missing ${env}; refusing to invent boot configuration"

  prefix="$(awk -F= '$1=="overlay_prefix"{print $2; exit}' "$env")"
  prefix="${prefix:-rockchip}"
  overlay_file="/boot/dtb/rockchip/overlay/${prefix}-${name}.dtbo"
  if [[ ! -f "$overlay_file" ]]; then
    # Fall back to versioned dtb path
    overlay_file="$(ls -1 /boot/dtb-*/rockchip/overlay/${prefix}-${name}.dtbo 2>/dev/null | head -n1 || true)"
  fi
  if [[ -z "$overlay_file" || ! -f "$overlay_file" ]]; then
    die "Overlay file for ${name} not found under /boot/dtb*/rockchip/overlay/"
  fi
  cot_r_info "Overlay blob: ${overlay_file}"

  if cot_r_overlay_listed "$name"; then
    cot_r_ok "Overlay already listed: ${name}"
    cot_r_manifest_add_overlay "$name"
    # Bus may still be absent until reboot after a previous prepare.
    if [[ ! -e /dev/i2c-7 && ! -e /sys/bus/i2c/devices/i2c-7 ]]; then
      reboot_needed=yes
      cot_r_warn "i2c-7 not present; a reboot may still be required for ${name}"
    fi
    printf 'reboot_needed=%s\n' "$reboot_needed"
    return 0
  fi

  backup_file "$env" >/dev/null
  if grep -q '^overlays=' "$env"; then
    # Append token to existing overlays= line
    local tmp
    tmp="$(mktemp)"
    awk -v n="$name" '
      BEGIN { done=0 }
      /^overlays=/ && !done {
        if ($0 ~ "(^|[ =])" n "( |$)") { print; done=1; next }
        print $0 " " n
        done=1
        next
      }
      { print }
      END {
        if (!done) print "overlays=" n
      }
    ' "$env" >"$tmp"
    mv -f "$tmp" "$env"
  else
    printf '\noverlays=%s\n' "$name" >>"$env"
  fi

  if ! cot_r_overlay_listed "$name"; then
    die "Failed to record overlay ${name} in ${env}"
  fi
  cot_r_manifest_add_overlay "$name"
  cot_r_manifest_add_file "$env" "boot-config"
  reboot_needed=yes
  cot_r_ok "Added overlay ${name} to ${env}"
  cot_r_warn "Reboot required before I2C7 / OLED bus is available (not performing reboot)"
  printf 'reboot_needed=%s\n' "$reboot_needed"
}

cot_r_ensure_i2c7_overlay() {
  cot_r_ensure_overlay "${COT_R_OVERLAY_I2C7}"
}
