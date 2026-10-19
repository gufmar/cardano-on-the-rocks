#!/usr/bin/env python3
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Unix-socket OLED display server.

Environment (from /etc/cot-r/display.env):
  COT_R_I2C_BUS, COT_R_I2C_ADDRESS, COT_R_DISPLAY_SOCKET,
  COT_R_DISPLAY_MAX_FPS, COT_R_DISPLAY_ORIENTATION
"""

from __future__ import annotations

import heapq
import logging
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Allow running from /opt/cot-r/display as well as repo checkout
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from cot_r_display import SOURCE_URL, __version__  # noqa: E402
from cot_r_display.protocol import (  # noqa: E402
    MAX_ANIM_SECONDS,
    MAX_FPS,
    MAX_LINE_BYTES,
    MAX_QUEUE,
    DisplayMessage,
    encode_reply,
    parse_line,
    text_lines,
)
from cot_r_display.renderer import Renderer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("cot_r_display.server")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw, 0)


@dataclass(order=True)
class _Queued:
    # heapq is min-heap; negate priority so higher priority comes first
    sort_key: Tuple[int, float] = field(compare=True)
    message: DisplayMessage = field(compare=False)


class DisplayServer:
    def __init__(self) -> None:
        self.sock_path = os.environ.get("COT_R_DISPLAY_SOCKET", "/run/cot-r/display.sock")
        self.max_fps = min(_env_int("COT_R_DISPLAY_MAX_FPS", MAX_FPS), MAX_FPS)
        bus = _env_int("COT_R_I2C_BUS", 7)
        addr = _env_int("COT_R_I2C_ADDRESS", 0x3C)
        rotate = _env_int("COT_R_DISPLAY_ORIENTATION", 0)
        self.renderer = Renderer(bus=bus, address=addr, rotate=rotate)
        self._q: List[_Queued] = []
        self._q_lock = threading.Lock()
        self._seq = 0
        self._stop = threading.Event()
        self._current: Optional[DisplayMessage] = None
        self._current_deadline = 0.0

    def enqueue(self, msg: DisplayMessage) -> None:
        # Bound animation duration via ttl
        if msg.ttl_ms > MAX_ANIM_SECONDS * 1000:
            msg.ttl_ms = MAX_ANIM_SECONDS * 1000
        with self._q_lock:
            # Preempt: if incoming priority beats current, clear lower-priority queue
            if self._current and msg.priority > self._current.priority:
                self._q = [item for item in self._q if item.message.priority >= msg.priority]
                heapq.heapify(self._q)
                self._current_deadline = 0.0
            while len(self._q) >= MAX_QUEUE:
                heapq.heappop(self._q)
            self._seq += 1
            # sort: higher priority first, then earlier seq
            sort_key = (-msg.priority, float(self._seq))
            heapq.heappush(self._q, _Queued(sort_key=sort_key, message=msg))

    def _worker(self) -> None:
        min_interval = 1.0 / max(1, self.max_fps)
        while not self._stop.is_set():
            now = time.monotonic()
            msg: Optional[DisplayMessage] = None
            with self._q_lock:
                if self._current and now < self._current_deadline and not self._q:
                    pass
                elif self._q:
                    item = heapq.heappop(self._q)
                    msg = item.message
                    self._current = msg
                    self._current_deadline = now + (msg.ttl_ms / 1000.0)
                elif self._current and now >= self._current_deadline:
                    self._current = None
            if msg is not None:
                try:
                    self.renderer.apply(msg)
                except Exception as e:  # noqa: BLE001
                    log.exception("render failed: %s", e)
            time.sleep(min_interval)

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_LINE_BYTES:
                    conn.sendall(encode_reply(False, error="line too large"))
                    break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = parse_line(line)
                        if msg.cmd == "ping":
                            conn.sendall(
                                encode_reply(
                                    True,
                                    pong=True,
                                    version=__version__,
                                    hardware_ok=self.renderer.hardware_ok,
                                    source=SOURCE_URL,
                                )
                            )
                            continue
                        if msg.cmd == "status":
                            conn.sendall(
                                encode_reply(
                                    True,
                                    version=__version__,
                                    hardware_ok=self.renderer.hardware_ok,
                                    queue=len(self._q),
                                    source=SOURCE_URL,
                                )
                            )
                            continue
                        self.enqueue(msg)
                        conn.sendall(encode_reply(True, accepted=True, cmd=msg.cmd))
                    except Exception as e:  # noqa: BLE001
                        conn.sendall(encode_reply(False, error=str(e)))

    def serve(self) -> int:
        # Show boot splash (or null)
        self.enqueue(
            text_lines(
                ["Cardano Rocks", "default setup ready."],
                ttl_ms=8000,
            )
        )
        worker = threading.Thread(target=self._worker, name="display-worker", daemon=True)
        worker.start()

        if os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
        os.makedirs(os.path.dirname(self.sock_path), exist_ok=True)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.sock_path)
        try:
            os.chmod(self.sock_path, 0o660)
        except OSError:
            pass
        sock.listen(5)
        sock.settimeout(1.0)
        log.info(
            "listening on %s hardware_ok=%s version=%s",
            self.sock_path,
            self.renderer.hardware_ok,
            __version__,
        )

        def _stop(*_args):
            self._stop.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while not self._stop.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            t.start()

        sock.close()
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
        log.info("shutdown")
        return 0


def main() -> int:
    return DisplayServer().serve()


if __name__ == "__main__":
    sys.exit(main())
