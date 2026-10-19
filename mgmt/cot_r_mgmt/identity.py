# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Durable device identity and per-start boot ID."""

from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path
from typing import Optional

ETC_DIR = Path(os.environ.get("COT_R_ETC_DIR", "/etc/cot-r"))
RUN_DIR = Path(os.environ.get("COT_R_RUN_DIR", "/run/cot-r"))
DEVICE_ID_FILE = ETC_DIR / "device.id"
BOOT_ID_FILE = RUN_DIR / "boot.id"
DEVICE_ENV = ETC_DIR / "device.env"


def _read_env_value(path: Path, key: str) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def _read_text_if_allowed(path: Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def ensure_device_id() -> str:
    """Return stable device UUID; create under /etc/cot-r if missing and writable."""
    existing = _read_text_if_allowed(DEVICE_ID_FILE)
    if existing is not None:
        value = existing.strip()
        if value:
            return value
    try:
        ETC_DIR.mkdir(parents=True, exist_ok=True)
        value = str(uuid.uuid4())
        DEVICE_ID_FILE.write_text(value + "\n", encoding="utf-8")
        try:
            os.chmod(DEVICE_ID_FILE, 0o640)
        except OSError:
            pass
        return value
    except OSError:
        # Non-root service: fall back to ephemeral id for this process only.
        return str(uuid.uuid4())


def device_label() -> str:
    named = _read_env_value(DEVICE_ENV, "COT_R_DEVICE_ID")
    if named and named not in ("rockpi-unset", "unset", ""):
        return named
    return socket.gethostname()


def new_boot_id() -> str:
    """Generate a fresh boot ID for this management process start."""
    value = str(uuid.uuid4())
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        BOOT_ID_FILE.write_text(value + "\n", encoding="utf-8")
        try:
            os.chmod(BOOT_ID_FILE, 0o644)
        except OSError:
            pass
    except OSError:
        pass
    return value


def read_boot_id() -> Optional[str]:
    text = _read_text_if_allowed(BOOT_ID_FILE)
    if text is None:
        return None
    value = text.strip()
    return value or None
