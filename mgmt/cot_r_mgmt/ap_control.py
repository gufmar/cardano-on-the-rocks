# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Best-effort AP standby for waiting peers (stop/start hostapd via sudo)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Tuple

log = logging.getLogger("cot_r_mgmt.ap_control")

UNIT = "cot-r-hostapd.service"


def _systemctl(*args: str) -> Tuple[bool, str]:
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    sudo = shutil.which("sudo") or "/usr/bin/sudo"
    cmd = [sudo, "-n", systemctl, *args, UNIT]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or f"exit {proc.returncode}"
    return True, (proc.stdout or "").strip()


def standby() -> bool:
    """Disable the local AP SSID while keeping Ethernet/bridge management."""
    ok, msg = _systemctl("stop")
    if not ok:
        log.warning("AP standby (stop hostapd) failed: %s", msg)
        return False
    log.info("AP standby: hostapd stopped")
    return True


def resume() -> bool:
    """Re-enable local AP after returning to Initial."""
    ok, msg = _systemctl("start")
    if not ok:
        log.warning("AP resume (start hostapd) failed: %s", msg)
        return False
    log.info("AP resume: hostapd started")
    return True


def is_active() -> bool:
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    try:
        proc = subprocess.run(
            [systemctl, "is-active", UNIT],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.stdout.strip() == "active"
