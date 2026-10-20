# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Best-effort client for the local OLED display socket."""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any, Dict, List, Optional

log = logging.getLogger("cot_r_mgmt.display")

DEFAULT_SOCK = os.environ.get("COT_R_DISPLAY_SOCKET", "/run/cot-r/display.sock")


def send(msg: Dict[str, Any], sock_path: str = DEFAULT_SOCK, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(sock_path)
            sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        if not data:
            return None
        return json.loads(data.decode("utf-8").split("\n", 1)[0])
    except OSError as e:
        log.debug("display unavailable: %s", e)
        return None
    except (json.JSONDecodeError, UnicodeError) as e:
        log.debug("display bad reply: %s", e)
        return None


def show_text(lines: List[str], *, priority: int = 80, ttl_ms: int = 8000) -> None:
    send(
        {
            "cmd": "text",
            "priority": priority,
            "ttl_ms": ttl_ms,
            "payload": {"lines": lines[:5], "align": "left"},
        }
    )


def show_reset_notice() -> None:
    show_text(["Demo reset", "Initial state", "setup ready."], priority=80, ttl_ms=10000)


def show_waiting(*, coordinator_label: Optional[str] = None) -> None:
    """Persistent waiting screen for peers that acknowledged a coordinator."""
    line2 = (coordinator_label or "coordinator")[:18]
    send(
        {
            "cmd": "text",
            "priority": 60,
            "ttl_ms": 20000,
            "payload": {
                "lines": ["Waiting for setup", line2, "..."],
                "align": "left",
            },
        }
    )


def show_onboarding(
    *,
    step: int,
    digit: str,
    lines: List[str],
    qr_data: str,
    qr_side: str = "right",
    priority: int = 55,
    ttl_ms: int = 15000,
) -> None:
    """Two-step join flow: Wi-Fi QR (1) then local setup URL QR (2)."""
    send(
        {
            "cmd": "onboarding",
            "priority": priority,
            "ttl_ms": ttl_ms,
            "payload": {
                "step": step,
                "digit": digit,
                "lines": lines[:3],
                "qr_data": qr_data,
                "qr_side": qr_side,
            },
        }
    )
