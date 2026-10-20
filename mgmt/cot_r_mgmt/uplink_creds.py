# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Local store of known uplink SSIDs / PSKs (never exposed to the browser)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

log = logging.getLogger("cot_r_mgmt.uplink_creds")

ETC_DIR = Path(os.environ.get("COT_R_ETC_DIR", "/etc/cot-r"))
CREDS_FILE = Path(
    os.environ.get("COT_R_UPLINK_CREDS_FILE", str(ETC_DIR / "uplink-credentials.json"))
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read() -> Dict[str, Any]:
    if not CREDS_FILE.is_file():
        return {"version": 1, "networks": {}}
    try:
        data = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("uplink creds unreadable: %s", e)
        return {"version": 1, "networks": {}}
    if not isinstance(data, dict):
        return {"version": 1, "networks": {}}
    nets = data.get("networks")
    if not isinstance(nets, dict):
        data["networks"] = {}
    data["version"] = 1
    return data


def _write(data: Dict[str, Any]) -> None:
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    # Atomic replace; keep mode private.
    fd, tmp = tempfile.mkstemp(dir=str(CREDS_FILE.parent), prefix=".uplink-creds.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CREDS_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(CREDS_FILE, 0o600)
    except OSError:
        pass


def known_ssids() -> Set[str]:
    nets = _read().get("networks") or {}
    out: Set[str] = set()
    for ssid, meta in nets.items():
        if not isinstance(ssid, str) or not ssid:
            continue
        if isinstance(meta, dict) and meta.get("psk"):
            out.add(ssid)
        elif isinstance(meta, dict) and meta.get("has_psk"):
            out.add(ssid)
    return out


def get_psk(ssid: str) -> Optional[str]:
    nets = _read().get("networks") or {}
    meta = nets.get(ssid)
    if not isinstance(meta, dict):
        return None
    psk = meta.get("psk")
    if isinstance(psk, str) and psk:
        return psk
    return None


def has_psk(ssid: str) -> bool:
    return get_psk(ssid) is not None


def save_psk(ssid: str, psk: str) -> None:
    data = _read()
    nets = data.setdefault("networks", {})
    nets[ssid] = {
        "has_psk": True,
        "psk": psk,
        "updated_at": _now(),
    }
    _write(data)
    log.info("stored uplink credential for ssid=%r", ssid)


def mark_open(ssid: str) -> None:
    """Remember an open network was used (no PSK). Does not clear an existing PSK."""
    data = _read()
    nets = data.setdefault("networks", {})
    prev = nets.get(ssid) if isinstance(nets.get(ssid), dict) else {}
    if isinstance(prev, dict) and prev.get("psk"):
        return
    nets[ssid] = {"has_psk": False, "open": True, "updated_at": _now()}
    _write(data)