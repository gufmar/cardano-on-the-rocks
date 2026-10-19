# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Framebuffer helpers for 128x64 monochrome OLED."""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence, Tuple

from . import SOURCE_URL, __version__
from .protocol import DisplayMessage

log = logging.getLogger("cot_r_display.renderer")


class NullDevice:
    """Stand-in when hardware is missing; keeps the service alive."""

    width = 128
    height = 64
    bounding_box = (0, 0, 127, 63)

    def cleanup(self) -> None:
        return None


class Renderer:
    def __init__(
        self,
        bus: int = 7,
        address: int = 0x3C,
        width: int = 128,
        height: int = 64,
        rotate: int = 0,
    ) -> None:
        self.bus = bus
        self.address = address
        self.width = width
        self.height = height
        self.rotate = rotate
        self.device: Any = NullDevice()
        self.hardware_ok = False
        self._font = None
        self._init_device()

    def _init_device(self) -> None:
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
        except ImportError as e:
            log.warning("luma.oled not available: %s; running null renderer", e)
            return
        try:
            serial = i2c(port=self.bus, address=self.address)
            self.device = ssd1306(
                serial, width=self.width, height=self.height, rotate=self.rotate
            )
            self.hardware_ok = True
            log.info("SSD1306 ready on i2c-%s addr=%s", self.bus, hex(self.address))
        except Exception as e:  # noqa: BLE001 - hardware may be absent
            log.warning("OLED init failed (continuing without panel): %s", e)
            self.device = NullDevice()
            self.hardware_ok = False

    def _canvas(self):
        if not self.hardware_ok:
            return _NullCanvas()
        from luma.core.render import canvas

        return canvas(self.device)

    def apply(self, msg: DisplayMessage) -> None:
        cmd = msg.cmd
        if cmd == "noop":
            return
        if cmd == "clear":
            self.clear()
            return
        if cmd == "about":
            self.draw_text(
                [
                    "Cardano Rocks",
                    f"v{__version__}",
                    "AGPL-3.0",
                    "github.com/gufmar",
                ]
            )
            return
        if cmd == "text":
            lines = list(msg.payload.get("lines") or [])
            align = str(msg.payload.get("align") or "left")
            self.draw_text(lines, align=align)
            return
        if cmd == "rect":
            self.draw_rect(
                int(msg.payload.get("x", 0)),
                int(msg.payload.get("y", 0)),
                int(msg.payload.get("w", 10)),
                int(msg.payload.get("h", 10)),
                fill=bool(msg.payload.get("fill", False)),
            )
            return
        if cmd == "qr":
            data = str(msg.payload.get("data") or "")
            self.draw_qr(data)
            return
        if cmd == "bitmap":
            # payload: width, height, rows as list of hex strings (1 bit/pixel MSB)
            self.draw_bitmap(msg.payload)
            return
        log.warning("unknown display cmd: %s", cmd)

    def clear(self) -> None:
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")

    def draw_text(self, lines: Sequence[str], align: str = "left") -> None:
        lines = [str(x)[:21] for x in lines[:5]]
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            y = 2
            for line in lines:
                x = 2
                if align == "center":
                    # Approximate 6px/char default font
                    x = max(0, (self.width - len(line) * 6) // 2)
                elif align == "right":
                    x = max(0, self.width - len(line) * 6 - 2)
                draw.text((x, y), line, fill="white")
                y += 12

    def draw_rect(self, x: int, y: int, w: int, h: int, fill: bool = False) -> None:
        with self._canvas() as draw:
            draw.rectangle(
                (x, y, x + w, y + h),
                outline="white",
                fill="white" if fill else "black",
            )

    def draw_qr(self, data: str) -> None:
        """Render a compact QR. Reports limitation when payload is too dense."""
        if not data:
            self.draw_text(["QR empty"], align="center")
            return
        try:
            import qrcode
        except ImportError:
            self.draw_text(["qrcode pkg", "missing"], align="center")
            return
        # Keep modules large enough to scan: box_size computed for 128px with quiet zone.
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            border=2,
            box_size=1,
        )
        qr.add_data(data)
        try:
            qr.make(fit=True)
        except Exception as e:  # noqa: BLE001
            log.warning("QR encode failed: %s", e)
            self.draw_text(["QR too long", "use setup URL"], align="center")
            return
        matrix = qr.get_matrix()
        modules = len(matrix)
        # Leave a few pixels margin; 64 height is the hard limit on this panel.
        max_side = min(self.width, self.height) - 4
        scale = max_side // modules if modules else 0
        if scale < 2:
            # Unreliable on phones at 1px modules on 128x64.
            log.warning(
                "QR payload needs %s modules; scale=%s (unreliable). Fallback text.",
                modules,
                scale,
            )
            preview = data if len(data) <= 40 else data[:37] + "..."
            self.draw_text(["QR too dense", preview[:21], "see setup URL"], align="left")
            return
        size = modules * scale
        ox = (self.width - size) // 2
        oy = (self.height - size) // 2
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            for r, row in enumerate(matrix):
                for c, cell in enumerate(row):
                    if not cell:
                        continue
                    x0 = ox + c * scale
                    y0 = oy + r * scale
                    draw.rectangle(
                        (x0, y0, x0 + scale - 1, y0 + scale - 1),
                        outline="white",
                        fill="white",
                    )

    def draw_bitmap(self, payload: dict) -> None:
        w = int(payload.get("width") or 0)
        h = int(payload.get("height") or 0)
        rows: List[str] = list(payload.get("rows") or [])
        if w <= 0 or h <= 0 or w > self.width or h > self.height or len(rows) != h:
            self.draw_text(["bad bitmap"], align="center")
            return
        ox = int(payload.get("x") or 0)
        oy = int(payload.get("y") or 0)
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            for y, hexrow in enumerate(rows):
                try:
                    raw = bytes.fromhex(hexrow)
                except ValueError:
                    continue
                for x in range(w):
                    byte = raw[x // 8] if x // 8 < len(raw) else 0
                    bit = (byte >> (7 - (x % 8))) & 1
                    if bit:
                        draw.point((ox + x, oy + y), fill="white")


class _NullCanvas:
    def __enter__(self):
        return _NullDraw()

    def __exit__(self, *args):
        return False


class _NullDraw:
    def rectangle(self, *args, **kwargs):
        return None

    def text(self, *args, **kwargs):
        return None

    def point(self, *args, **kwargs):
        return None
