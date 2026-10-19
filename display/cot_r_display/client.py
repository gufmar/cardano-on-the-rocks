#!/usr/bin/env python3
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Minimal Unix-socket client for cot-r-display."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Any, Dict

DEFAULT_SOCK = os.environ.get("COT_R_DISPLAY_SOCKET", "/run/cot-r/display.sock")


def request(msg: Dict[str, Any], sock_path: str = DEFAULT_SOCK, timeout: float = 2.0) -> Dict[str, Any]:
    line = (json.dumps(msg) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(sock_path)
        sock.sendall(line)
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("no reply from display service")
    return json.loads(data.decode("utf-8").split("\n", 1)[0])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Send a command to cot-r-display")
    p.add_argument("--socket", default=DEFAULT_SOCK)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sub.add_parser("status")
    sub.add_parser("about")
    sub.add_parser("clear")

    t = sub.add_parser("text")
    t.add_argument("lines", nargs="+")
    t.add_argument("--priority", type=int, default=50)
    t.add_argument("--align", default="left", choices=["left", "center", "right"])

    q = sub.add_parser("qr")
    q.add_argument("data")
    q.add_argument("--priority", type=int, default=50)

    args = p.parse_args(argv)
    if args.cmd == "ping":
        msg = {"cmd": "ping"}
    elif args.cmd == "status":
        msg = {"cmd": "status"}
    elif args.cmd == "about":
        msg = {"cmd": "about", "priority": 50, "ttl_ms": 8000, "payload": {}}
    elif args.cmd == "clear":
        msg = {"cmd": "clear", "priority": 80, "ttl_ms": 1000, "payload": {}}
    elif args.cmd == "text":
        msg = {
            "cmd": "text",
            "priority": args.priority,
            "ttl_ms": 8000,
            "payload": {"lines": args.lines, "align": args.align},
        }
    elif args.cmd == "qr":
        msg = {
            "cmd": "qr",
            "priority": args.priority,
            "ttl_ms": 15000,
            "payload": {"data": args.data},
        }
    else:
        p.error(f"unknown cmd {args.cmd}")
        return 2

    try:
        reply = request(msg, sock_path=args.socket)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(reply, indent=2))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
