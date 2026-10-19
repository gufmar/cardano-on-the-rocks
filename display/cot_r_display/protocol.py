# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""JSON-lines protocol for the local display Unix socket.

Priorities (higher wins / can preempt):
  100  error / recovery
  80   reset / system notice
  50   status / setup
  20   animation / idle
  10   background

Limits are enforced by the server (payload size, fps, duration).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PRIORITY_ERROR = 100
PRIORITY_RESET = 80
PRIORITY_STATUS = 50
PRIORITY_ANIM = 20
PRIORITY_BG = 10

MAX_LINE_BYTES = 64 * 1024
MAX_QUEUE = 8
MAX_FPS = 10
MAX_ANIM_SECONDS = 60
MAX_BITMAP_BYTES = 16 * 1024


@dataclass
class DisplayMessage:
    cmd: str
    priority: int = PRIORITY_STATUS
    ttl_ms: int = 5000
    payload: Dict[str, Any] = field(default_factory=dict)
    msg_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisplayMessage":
        return cls(
            cmd=str(data.get("cmd", "noop")),
            priority=int(data.get("priority", PRIORITY_STATUS)),
            ttl_ms=int(data.get("ttl_ms", 5000)),
            payload=dict(data.get("payload") or {}),
            msg_id=data.get("msg_id"),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "cmd": self.cmd,
            "priority": self.priority,
            "ttl_ms": self.ttl_ms,
            "payload": self.payload,
        }
        if self.msg_id is not None:
            out["msg_id"] = self.msg_id
        return out


def parse_line(line: bytes) -> DisplayMessage:
    if len(line) > MAX_LINE_BYTES:
        raise ValueError("line too large")
    text = line.decode("utf-8").strip()
    if not text:
        raise ValueError("empty line")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("message must be a JSON object")
    return DisplayMessage.from_dict(data)


def encode_reply(ok: bool, **kwargs: Any) -> bytes:
    body = {"ok": ok, **kwargs}
    return (json.dumps(body, separators=(",", ":")) + "\n").encode("utf-8")


# Convenience constructors used by clients / tests

def text_lines(
    lines: List[str],
    *,
    priority: int = PRIORITY_STATUS,
    align: str = "left",
    ttl_ms: int = 8000,
) -> DisplayMessage:
    return DisplayMessage(
        cmd="text",
        priority=priority,
        ttl_ms=ttl_ms,
        payload={"lines": lines[:6], "align": align},
    )


def clear(*, priority: int = PRIORITY_RESET) -> DisplayMessage:
    return DisplayMessage(cmd="clear", priority=priority, ttl_ms=1000, payload={})


def about(*, priority: int = PRIORITY_STATUS) -> DisplayMessage:
    return DisplayMessage(cmd="about", priority=priority, ttl_ms=10000, payload={})
