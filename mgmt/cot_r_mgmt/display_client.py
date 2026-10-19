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
