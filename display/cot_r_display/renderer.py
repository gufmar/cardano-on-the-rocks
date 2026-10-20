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
        if cmd == "onboarding":
            self.draw_onboarding(msg.payload)
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

    def _qr_matrix(self, data: str) -> Optional[List[List[bool]]]:
        if not data:
            return None
        try:
            import qrcode
        except ImportError:
            return None
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            border=1,
            box_size=1,
        )
        qr.add_data(data)
        try:
            qr.make(fit=True)
        except Exception as e:  # noqa: BLE001
            log.warning("QR encode failed: %s", e)
            return None
        return qr.get_matrix()

    def _blit_qr(
        self,
        draw: Any,
        matrix: List[List[bool]],
        *,
        max_w: int,
        max_h: int,
        ox: int,
        oy: int,
    ) -> bool:
        modules = len(matrix)
        scale = min(max_w, max_h) // modules if modules else 0
        if scale < 1:
            return False
        size = modules * scale
        x0 = ox + max(0, (max_w - size) // 2)
        y0 = oy + max(0, (max_h - size) // 2)
        for r, row in enumerate(matrix):
            for c, cell in enumerate(row):
                if not cell:
                    continue
                px = x0 + c * scale
                py = y0 + r * scale
                draw.rectangle(
                    (px, py, px + scale - 1, py + scale - 1),
                    outline="white",
                    fill="white",
                )
        return True

    def _big_font(self, size: int = 40):
        try:
            from PIL import ImageFont

            path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            return ImageFont.truetype(path, size=size)
        except Exception:  # noqa: BLE001
            return None

    def _small_font(self, size: int = 12):
        try:
            from PIL import ImageFont

            path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            return ImageFont.truetype(path, size=size)
        except Exception:  # noqa: BLE001
            return None

    def draw_qr(self, data: str) -> None:
        """Full-panel QR (legacy / ap-show-psk)."""
        matrix = self._qr_matrix(data)
        if matrix is None:
            self.draw_text(["QR empty" if not data else "QR fail"], align="center")
            return
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            if not self._blit_qr(
                draw, matrix, max_w=self.width, max_h=self.height, ox=0, oy=0
            ):
                preview = data if len(data) <= 40 else data[:37] + "..."
                draw.text((2, 2), "QR too dense", fill="white")
                draw.text((2, 14), preview[:21], fill="white")

    def draw_onboarding(self, payload: dict) -> None:
        """Split layout: step digit + caption on one side, full-height QR on the other.

        step 1: digit/text LEFT, Wi-Fi QR RIGHT
        step 2: setup-URL QR LEFT, digit/text RIGHT
        """
        step = int(payload.get("step") or 1)
        digit = str(payload.get("digit") or step)
        lines = [str(x) for x in (payload.get("lines") or [])][:3]
        qr_data = str(payload.get("qr_data") or "")
        # Default sides match the product story.
        qr_side = str(payload.get("qr_side") or ("right" if step == 1 else "left")).lower()
        if qr_side not in ("left", "right"):
            qr_side = "right"

        text_w = 60
        qr_w = self.width - text_w
        if qr_side == "right":
            text_ox, qr_ox = 0, text_w
        else:
            qr_ox, text_ox = 0, qr_w

        matrix = self._qr_matrix(qr_data)
        big = self._big_font(36)
        small = self._small_font(11)

        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            # Digit near top of text column
            if big is not None:
                draw.text((text_ox + 4, -2), digit[:2], font=big, fill="white")
                ty = 34
            else:
                draw.text((text_ox + 4, 0), digit[:2], fill="white")
                ty = 14
            for line in lines:
                if small is not None:
                    draw.text((text_ox + 2, ty), line[:10], font=small, fill="white")
                else:
                    draw.text((text_ox + 2, ty), line[:10], fill="white")
                ty += 12

            if matrix is None or not self._blit_qr(
                draw, matrix, max_w=qr_w, max_h=self.height, ox=qr_ox, oy=0
            ):
                draw.text((qr_ox + 2, 24), "QR n/a", fill="white")

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
