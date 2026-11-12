# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Framebuffer helpers for 128x64 monochrome OLED."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from . import SOURCE_URL, __version__
from .protocol import DisplayMessage

log = logging.getLogger("cot_r_display.renderer")

_ASSETS = Path(__file__).resolve().parent / "assets"
_AMARU_MARK_JSON = _ASSETS / "amaru-mark-48.json"


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
        driver: str = "auto",
    ) -> None:
        self.bus = bus
        self.address = address
        self.width = width
        self.height = height
        self.rotate = rotate
        self.driver = (driver or "auto").strip().lower()
        self.device: Any = NullDevice()
        self.hardware_ok = False
        self._font = None
        self._init_device()

    def _init_device(self) -> None:
        try:
            from luma.core.interface.serial import i2c
            from luma.oled import device as oled_dev
        except ImportError as e:
            log.warning("luma.oled not available: %s; running null renderer", e)
            return
        if self.driver in ("ssd1306", "sh1106"):
            candidates = [self.driver]
        else:
            # Many "SSD1306" shop listings are actually SH1106 silicon.
            candidates = ["ssd1306", "sh1106"]
        last: Optional[Exception] = None
        for name in candidates:
            cls = getattr(oled_dev, name, None)
            if cls is None:
                continue
            try:
                serial = i2c(port=self.bus, address=self.address)
                self.device = cls(
                    serial,
                    width=self.width,
                    height=self.height,
                    rotate=self.rotate,
                )
                self.hardware_ok = True
                self.driver = name
                log.info(
                    "%s ready on i2c-%s addr=%s",
                    name.upper(),
                    self.bus,
                    hex(self.address),
                )
                return
            except Exception as e:  # noqa: BLE001 - try next driver / absent panel
                last = e
                continue
        log.warning("OLED init failed (continuing without panel): %s", last)
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
        if cmd == "node_slide":
            self.draw_node_slide(msg.payload)
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

    def _qr_matrix(self, data: str, *, border: int = 1) -> Optional[List[List[bool]]]:
        if not data:
            return None
        try:
            import qrcode
        except ImportError:
            return None
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            border=border,
            box_size=1,
        )
        qr.add_data(data)
        try:
            qr.make(fit=True)
        except Exception as e:  # noqa: BLE001
            log.warning("QR encode failed: %s", e)
            return None
        return qr.get_matrix()

    def _qr_matrix_for_box(
        self, data: str, *, max_w: int, max_h: int
    ) -> Tuple[Optional[List[List[bool]]], int]:
        """Pick border 1 then 0; prefer the matrix that yields the largest integer scale."""
        best: Optional[List[List[bool]]] = None
        best_scale = 0
        side = min(max_w, max_h)
        for border in (1, 0):
            matrix = self._qr_matrix(data, border=border)
            if not matrix:
                continue
            modules = len(matrix)
            scale = side // modules if modules else 0
            if scale > best_scale:
                best = matrix
                best_scale = scale
            if scale >= 2:
                break
        return best, best_scale

    def _blit_qr(
        self,
        draw: Any,
        matrix: List[List[bool]],
        *,
        max_w: int,
        max_h: int,
        ox: int,
        oy: int,
    ) -> int:
        """Draw QR; returns module pixel scale (0 if it did not fit)."""
        modules = len(matrix)
        scale = min(max_w, max_h) // modules if modules else 0
        if scale < 1:
            return 0
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
        return scale

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
        matrix, scale = self._qr_matrix_for_box(data, max_w=self.width, max_h=self.height)
        if matrix is None:
            self.draw_text(["QR empty" if not data else "QR fail"], align="center")
            return
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            got = self._blit_qr(
                draw, matrix, max_w=self.width, max_h=self.height, ox=0, oy=0
            )
            if got < 1:
                preview = data if len(data) <= 40 else data[:37] + "..."
                draw.text((2, 2), "QR too dense", fill="white")
                draw.text((2, 14), preview[:21], fill="white")
            elif got < 2:
                log.warning("full-panel QR scale=%s modules=%s (hard to scan)", got, len(matrix))

    def _draw_gear(self, draw: Any, cx: int, cy: int, r: int = 18) -> None:
        """Simple gear icon (spokes + hub) for step-3 configuring."""
        import math

        teeth = 8
        for i in range(teeth):
            ang = (2 * math.pi * i) / teeth
            x1 = cx + int((r - 4) * math.cos(ang))
            y1 = cy + int((r - 4) * math.sin(ang))
            x2 = cx + int((r + 5) * math.cos(ang))
            y2 = cy + int((r + 5) * math.sin(ang))
            draw.line((x1, y1, x2, y2), fill="white", width=3)
        draw.ellipse((cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2), outline="white", width=2)
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline="white", fill="black", width=1)

    def draw_onboarding(self, payload: dict) -> None:
        """Onboarding layouts.

        step 1: digit/text LEFT, Wi-Fi QR RIGHT
        step 2: setup-URL QR LEFT, digit/text RIGHT
        step 3+: big 3 + gear (+ optional "controlled by / node X") — no QR
        """
        step = int(payload.get("step") or 1)
        digit = str(payload.get("digit") or step)
        lines = [str(x) for x in (payload.get("lines") or [])][:3]
        qr_data = str(payload.get("qr_data") or "")
        controlled_by = payload.get("controlled_by")

        if step >= 3:
            self._draw_configuring(digit=digit, lines=lines, controlled_by=controlled_by)
            return

        # Default sides match the product story.
        qr_side = str(payload.get("qr_side") or ("right" if step == 1 else "left")).lower()
        if qr_side not in ("left", "right"):
            qr_side = "right"

        # Hard 64px square for QR; remaining width (~64) for digit + caption.
        qr_w = min(self.width, self.height)  # 64 on this panel
        text_w = self.width - qr_w
        if qr_side == "right":
            text_ox, qr_ox = 0, text_w
        else:
            qr_ox, text_ox = 0, qr_w

        matrix, planned = self._qr_matrix_for_box(qr_data, max_w=qr_w, max_h=self.height)
        big = self._big_font(32)
        small = self._small_font(11)

        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            # Digit near top of text column (keep glyphs inside text_w).
            if big is not None:
                draw.text((text_ox + 2, -4), digit[:1], font=big, fill="white")
                ty = 30
            else:
                draw.text((text_ox + 2, 0), digit[:1], fill="white")
                ty = 14
            for line in lines:
                if small is not None:
                    draw.text((text_ox + 1, ty), line[:9], font=small, fill="white")
                else:
                    draw.text((text_ox + 1, ty), line[:9], fill="white")
                ty += 12

            if matrix is None:
                draw.text((qr_ox + 2, 24), "QR n/a", fill="white")
                return
            got = self._blit_qr(
                draw, matrix, max_w=qr_w, max_h=self.height, ox=qr_ox, oy=0
            )
            log.info(
                "onboarding QR step=%s modules=%s planned_scale=%s got_scale=%s qr_w=%s",
                step,
                len(matrix),
                planned,
                got,
                qr_w,
            )
            if got < 2:
                # Visible hint instead of an unscannable speck.
                draw.rectangle((qr_ox, 50, qr_ox + qr_w - 1, 63), outline="black", fill="black")
                draw.text((qr_ox + 2, 52), "PSK too long", fill="white")

    def _draw_configuring(
        self,
        *,
        digit: str,
        lines: Sequence[str],
        controlled_by: Any = None,
    ) -> None:
        big = self._big_font(32)
        small = self._small_font(11)
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            if big is not None:
                draw.text((2, -4), (digit or "3")[:1], font=big, fill="white")
            else:
                draw.text((2, 0), (digit or "3")[:1], fill="white")
            self._draw_gear(draw, cx=96, cy=28, r=16)
            if controlled_by:
                # Room under the gear: two short lines, same font as the rest.
                line1 = "controlled by"
                line2 = f"node {controlled_by}"
                if small is not None:
                    draw.text((2, 40), line1[:21], font=small, fill="white")
                    draw.text((2, 52), line2[:21], font=small, fill="white")
                else:
                    draw.text((2, 40), line1[:21], fill="white")
                    draw.text((2, 52), line2[:21], fill="white")
            else:
                ty = 40
                caps = list(lines) if lines else ["setup"]
                for line in caps[:2]:
                    if small is not None:
                        draw.text((2, ty), str(line)[:18], font=small, fill="white")
                    else:
                        draw.text((2, ty), str(line)[:18], fill="white")
                    ty += 12

    def _fmt_num(self, value: Any, digits: int = 1) -> str:
        if value is None:
            return "—"
        try:
            if isinstance(value, float):
                return f"{value:.{digits}f}"
            return str(int(value))
        except (TypeError, ValueError):
            return "—"

    def _load_amaru_mark(self) -> Optional[dict]:
        try:
            data = json.loads(_AMARU_MARK_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("amaru mark missing: %s", e)
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _blit_bitmap(
        self,
        draw: Any,
        mark: dict,
        *,
        ox: int,
        oy: int,
    ) -> None:
        w = int(mark.get("width") or 0)
        h = int(mark.get("height") or 0)
        rows: List[str] = list(mark.get("rows") or [])
        if w <= 0 or h <= 0 or len(rows) != h:
            return
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

    def _draw_iso_block_small(self, draw: Any, *, x0: int, y0: int, ox: int = 0) -> None:
        """Compact isometric block glyph (no text inside)."""
        x = x0 + ox
        y = y0
        w, h, d = 18, 14, 4
        top = [
            (x + d, y),
            (x + w + d, y),
            (x + w, y + d),
            (x, y + d),
        ]
        draw.polygon(top, outline="white", fill="black")
        draw.rectangle((x, y + d, x + w, y + d + h), outline="white", fill="black")
        side = [
            (x + w, y + d),
            (x + w + d, y),
            (x + w + d, y + h),
            (x + w, y + d + h),
        ]
        draw.polygon(side, outline="white", fill="black")

    def _draw_cpu_core_bars(
        self,
        draw: Any,
        cores: Sequence[float],
        *,
        x0: int,
        y0: int,
        bar_w: int,
        bar_h: int,
        gap: int,
    ) -> None:
        for i, pct in enumerate(cores):
            x = x0 + i * (bar_w + gap)
            # Outer border = 100% capacity.
            draw.rectangle((x, y0, x + bar_w, y0 + bar_h), outline="white", fill="black")
            try:
                p = float(pct)
            except (TypeError, ValueError):
                p = 0.0
            fill_h = int(round(bar_h * max(0.0, min(1.0, p / 100.0))))
            if fill_h <= 0:
                continue
            # Inset fill so the border stays visible.
            draw.rectangle(
                (x + 1, y0 + bar_h - fill_h, x + bar_w - 1, y0 + bar_h - 1),
                outline="white",
                fill="white",
            )

    def _draw_mem_bars(
        self,
        draw: Any,
        *,
        os_used: Optional[float],
        os_total: Optional[float],
        node_rss: Optional[float],
        node_running: bool,
    ) -> None:
        small = self._small_font(11)
        total = float(os_total or 0.0) or 4096.0
        os_u = float(os_used or 0.0)
        node = float(node_rss or 0.0) if node_running else 0.0

        def mlabel(v: float) -> str:
            if v >= 1024:
                return f"{v / 1024.0:.1f}G"
            return f"{int(round(v))}M"

        top = f"Mem OS {mlabel(os_u)}/{mlabel(total)}"
        bot = (
            f"Mem Node {mlabel(node)}"
            if node_running
            else "Mem Node stopped"
        )
        if small is not None:
            draw.text((2, 1), top[:21], font=small, fill="white")
            draw.text((2, 52), bot[:21], font=small, fill="white")
        else:
            draw.text((2, 1), top[:21], fill="white")
            draw.text((2, 52), bot[:21], fill="white")

        # Outer border = full board RAM.
        x0, y0, x1, y1 = 2, 16, 125, 48
        draw.rectangle((x0, y0, x1, y1), outline="white", fill="black")
        inner_w = x1 - x0 - 4
        os_fill = int(round(inner_w * max(0.0, min(1.0, os_u / total))))
        node_fill = int(round(inner_w * max(0.0, min(1.0, node / total))))
        # Two stacked bars inside the border.
        if os_fill > 0:
            draw.rectangle((x0 + 2, y0 + 3, x0 + 2 + os_fill, y0 + 14), outline="white", fill="white")
        if node_fill > 0:
            draw.rectangle(
                (x0 + 2, y0 + 18, x0 + 2 + node_fill, y1 - 3),
                outline="white",
                fill="white",
            )

    def draw_node_slide(self, payload: dict) -> None:
        """Wizard Nodes OLED slides (128×64)."""
        kind = str(payload.get("kind") or "progress")
        if kind == "tip":
            self._draw_tip_slide(payload)
            return
        if kind == "logo":
            self._draw_logo_slide()
            return
        if kind == "qr":
            self._draw_node_qr_slide(payload)
            return
        if kind == "cpu":
            self._draw_cpu_slide(payload)
            return
        if kind == "memory":
            with self._canvas() as draw:
                draw.rectangle(self.device.bounding_box, outline="black", fill="black")
                self._draw_mem_bars(
                    draw,
                    os_used=payload.get("os_used"),
                    os_total=payload.get("os_total"),
                    node_rss=payload.get("node_rss"),
                    node_running=bool(payload.get("node_running")),
                )
            return
        small = self._small_font(11)
        big = self._small_font(13)
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            if kind == "progress":
                lines = [
                    str(payload.get("title") or "Amaru")[:21],
                    str(payload.get("line2") or "")[:21],
                    str(payload.get("line3") or "")[:21],
                ]
            elif kind == "mempool":
                mp = payload.get("mempool_tx")
                last_tx = payload.get("last_block_tx")
                block = payload.get("block_no")
                lines = [
                    "Mempool",
                    f"pending {self._fmt_num(mp)}" if mp is not None else "pending n/a",
                    f"blk txs {self._fmt_num(last_tx)}"
                    + (f" @{self._fmt_num(block)}" if block is not None else ""),
                ]
            else:
                lines = [kind[:21], "unknown slide"]

            y = 4
            for i, line in enumerate(lines[:4]):
                font = big if i == 0 else small
                if font is not None:
                    draw.text((2, y), line[:21], font=font, fill="white")
                else:
                    draw.text((2, y), line[:21], fill="white")
                y += 14 if i == 0 else 13

    def _draw_logo_slide(self) -> None:
        mark = self._load_amaru_mark()
        small = self._small_font(12)
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            if mark:
                w = int(mark.get("width") or 48)
                h = int(mark.get("height") or 48)
                ox = max(0, (self.width - w) // 2)
                oy = max(0, (self.height - h - 12) // 2)
                self._blit_bitmap(draw, mark, ox=ox, oy=oy)
            label = "Amaru"
            if small is not None:
                # Approximate center for DejaVu ~7px/char at 12pt.
                tw = len(label) * 7
                draw.text(
                    (max(0, (self.width - tw) // 2), 52),
                    label,
                    font=small,
                    fill="white",
                )
            else:
                draw.text((46, 52), label, fill="white")

    def _draw_cpu_slide(self, payload: dict) -> None:
        small = self._small_font(11)
        cores = list(payload.get("cores") or [])
        nproc = int(payload.get("nproc") or len(cores) or 0)
        cpu_c = self._fmt_num(payload.get("cpu_c"), 0)
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            title = f"CPU {nproc}c  {cpu_c}C"
            if small is not None:
                draw.text((2, 1), title[:21], font=small, fill="white")
            else:
                draw.text((2, 1), title[:21], fill="white")
            if not cores and nproc > 0:
                cores = [0.0] * nproc
            if not cores:
                load1 = self._fmt_num(payload.get("load1"))
                if small is not None:
                    draw.text((2, 28), f"load {load1}", font=small, fill="white")
                else:
                    draw.text((2, 28), f"load {load1}", fill="white")
                return
            # Fit bars across 128px with a 100%-height border each.
            n = len(cores)
            gap = 2 if n >= 6 else 3
            bar_w = max(8, min(16, (124 - gap * (n - 1)) // n))
            total_w = n * bar_w + (n - 1) * gap
            x0 = max(2, (self.width - total_w) // 2)
            self._draw_cpu_core_bars(
                draw, cores, x0=x0, y0=16, bar_w=bar_w, bar_h=42, gap=gap
            )

    def _draw_node_qr_slide(self, payload: dict) -> None:
        """Wi-Fi or status URL QR beside a short caption (same 64px column as onboarding)."""
        digit = str(payload.get("digit") or "")
        title = str(payload.get("title") or "QR")[:9]
        lines = [str(x) for x in (payload.get("lines") or [])][:2]
        qr_data = str(payload.get("qr_data") or "")
        qr_side = str(payload.get("qr_side") or "right").lower()
        if qr_side not in ("left", "right"):
            qr_side = "right"
        qr_w = min(self.width, self.height)
        text_w = self.width - qr_w
        if qr_side == "right":
            text_ox, qr_ox = 0, text_w
        else:
            qr_ox, text_ox = 0, qr_w
        matrix, _planned = self._qr_matrix_for_box(qr_data, max_w=qr_w, max_h=self.height)
        big = self._big_font(28)
        small = self._small_font(11)
        with self._canvas() as draw:
            draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            if digit and big is not None:
                draw.text((text_ox + 2, -2), digit[:1], font=big, fill="white")
                ty = 28
            elif digit:
                draw.text((text_ox + 2, 0), digit[:1], fill="white")
                ty = 14
            else:
                ty = 4
            caps = [title] + lines
            for line in caps[:3]:
                if small is not None:
                    draw.text((text_ox + 1, ty), line[:9], font=small, fill="white")
                else:
                    draw.text((text_ox + 1, ty), line[:9], fill="white")
                ty += 12
            if matrix is None:
                draw.text((qr_ox + 2, 24), "QR n/a", fill="white")
                return
            got = self._blit_qr(
                draw, matrix, max_w=qr_w, max_h=self.height, ox=qr_ox, oy=0
            )
            if got < 2:
                draw.rectangle((qr_ox, 50, qr_ox + qr_w - 1, 63), outline="black", fill="black")
                draw.text((qr_ox + 2, 52), "QR dense", fill="white")

    def _draw_tip_slide(self, payload: dict) -> None:
        block = payload.get("block_no")
        slot = payload.get("slot")
        gap = payload.get("gap_blocks")
        phase = str(payload.get("phase") or "")
        prev = payload.get("prev_block_no")
        animate = bool(payload.get("animate")) and block is not None and prev is not None
        label = self._fmt_num(block)
        small = self._small_font(11)
        # ~8-digit tip heights need ~13–14pt to fit right of the glyph.
        num = self._small_font(14) or self._small_font(12) or small

        def paint(ox: int) -> None:
            with self._canvas() as draw:
                draw.rectangle(self.device.bounding_box, outline="black", fill="black")
                if small is not None:
                    draw.text((2, 1), "Tip height", font=small, fill="white")
                else:
                    draw.text((2, 1), "Tip height", fill="white")
                # Small block on the left; block number uses most of the width.
                self._draw_iso_block_small(draw, x0=4, y0=20, ox=ox)
                tx = 32
                ty = 24
                if num is not None:
                    draw.text((tx, ty), label[:12], font=num, fill="white")
                else:
                    draw.text((tx, ty + 2), label[:12], fill="white")
                foot_bits = []
                if phase == "on_tip" or (gap is not None and int(gap) <= 300):
                    if gap is None or int(gap) == 0:
                        foot_bits.append("on tip")
                    else:
                        foot_bits.append(f"on tip Δ{int(gap)}")
                elif gap is not None:
                    foot_bits.append(f"−{int(gap)} blk")
                if slot is not None:
                    foot_bits.append(f"s{int(slot)}"[-8:])
                if foot_bits:
                    txt = " ".join(foot_bits)[:21]
                    if small is not None:
                        draw.text((2, 52), txt, font=small, fill="white")
                    else:
                        draw.text((2, 52), txt, fill="white")

        if not animate:
            paint(0)
            return
        for ox in (40, 24, 12, 4, 0):
            paint(ox)
            time.sleep(0.09)
        paint(0)

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

    def line(self, *args, **kwargs):
        return None

    def ellipse(self, *args, **kwargs):
        return None

    def polygon(self, *args, **kwargs):
        return None
