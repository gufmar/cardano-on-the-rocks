# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""AP onboarding OLED: steps 1 (Wi-Fi QR), 2 (setup URL), 3 (gear / configuring)."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, List, Optional

from cot_r_mgmt import display_client
from cot_r_mgmt import peers
from cot_r_mgmt.association import list_stations
from cot_r_mgmt.state import StateStore
from cot_r_mgmt.wizard import (
    STEP_CONFIGURING,
    STEP_SETUP_QR,
    STEP_WIFI_QR,
    WizardStore,
)

log = logging.getLogger("cot_r_mgmt.onboarding")

ETC_DIR = Path(os.environ.get("COT_R_ETC_DIR", "/etc/cot-r"))
AP_PSK_FILE = Path(os.environ.get("COT_R_AP_PSK_FILE", str(ETC_DIR / "ap.psk")))
AP_ENV = ETC_DIR / "ap.env"
AP_ENABLED_FLAG = Path(os.environ.get("COT_R_STATE_DIR", "/var/lib/cot-r")) / "ap-enabled"
POLL_SECONDS = float(os.environ.get("COT_R_ONBOARD_POLL", "2.0"))
MGMT_PORT = int(os.environ.get("COT_R_MGMT_PORT", "8080"))


def _wifi_escape(value: str) -> str:
    out: List[str] = []
    for ch in value:
        if ch in ("\\", ";", ",", '"', ":"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def wifi_qr_payload(ssid: str, psk: str) -> str:
    return f"WIFI:T:WPA;S:{_wifi_escape(ssid)};P:{_wifi_escape(psk)};H:false;;"


def setup_url(mgmt_ipv4: str, port: int = MGMT_PORT) -> str:
    return f"http://{mgmt_ipv4}:{port}/setup"


def read_ssid() -> Optional[str]:
    if not AP_ENV.is_file():
        return None
    try:
        for line in AP_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("COT_R_AP_SSID="):
                raw = line.split("=", 1)[1].strip()
                if (raw.startswith("'") and raw.endswith("'")) or (
                    raw.startswith('"') and raw.endswith('"')
                ):
                    raw = raw[1:-1]
                return raw or None
    except OSError:
        return None
    return None


def read_psk() -> Optional[str]:
    try:
        if not AP_PSK_FILE.is_file():
            return None
        return AP_PSK_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def board_digit() -> str:
    env_path = ETC_DIR / "device.env"
    device_id = ""
    reserved = ""
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("COT_R_DEVICE_ID="):
                    device_id = line.split("=", 1)[1].strip().strip("'\"")
                elif line.startswith("COT_R_RESERVED_IPV4="):
                    reserved = line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            pass
    if device_id.endswith("-1") or device_id in ("1", "rockpi-1"):
        return "1"
    if device_id.endswith("-2") or device_id in ("2", "rockpi-2"):
        return "2"
    if device_id.endswith("-3") or device_id in ("3", "rockpi-3"):
        return "3"
    last = reserved.rsplit(".", 1)[-1] if reserved else ""
    return {"195": "1", "196": "2", "194": "3"}.get(last, "?")


def ap_is_enabled() -> bool:
    return AP_ENABLED_FLAG.is_file()


class OnboardingDisplay:
    """Background driver for OLED onboarding / wizard steps."""

    def __init__(
        self,
        *,
        store: StateStore,
        wizard: WizardStore,
        should_run: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.store = store
        self.wizard = wizard
        self._should_run = should_run or self._default_should_run
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_step: Optional[int] = None
        self._seen_sta = False

    def _default_should_run(self) -> bool:
        wiz = self.wizard.snapshot()
        # Adopted / configuring boards keep step-3 OLED even if AP is off.
        if wiz.get("ui_step", 0) >= STEP_CONFIGURING or wiz.get("role") == "adopted":
            return True
        if not ap_is_enabled():
            return False
        st = self.store.snapshot()
        if st.get("role") == "peer" and st.get("device_state") == "Waiting":
            # Peer waiting on coordinator: still show waiting OR step3 if adopted
            if wiz.get("role") == "adopted":
                return True
            return False
        return True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cot-r-onboard", daemon=True)
        self._thread.start()
        log.info("onboarding display loop started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _loop(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("onboarding tick failed")

    def _tick(self) -> None:
        if not self._should_run():
            self._last_step = None
            return

        wiz = self.wizard.snapshot()
        if wiz.get("ui_step", STEP_WIFI_QR) >= STEP_CONFIGURING or wiz.get("confirmed"):
            display_client.show_onboarding(
                step=STEP_CONFIGURING,
                digit="3",
                lines=["setup"],
                qr_data="",
                qr_side="right",
                controlled_by=wiz.get("controlled_by"),
                priority=70,
                ttl_ms=60000,
            )
            if self._last_step != STEP_CONFIGURING:
                log.info(
                    "onboarding step=3 board=%s role=%s ctr=%s",
                    board_digit(),
                    wiz.get("role"),
                    wiz.get("controlled_by"),
                )
                self._last_step = STEP_CONFIGURING
            return

        # Steps 1–2 require AP credentials.
        ssid = read_ssid()
        psk = read_psk()
        if not ssid or not psk:
            return
        ip = peers.local_mgmt_ipv4()
        if not ip:
            return

        stations = list_stations()
        if stations:
            self._seen_sta = True
        step = STEP_SETUP_QR if self._seen_sta else STEP_WIFI_QR

        if step == STEP_WIFI_QR:
            display_client.show_onboarding(
                step=1,
                digit="1",
                lines=["connect", "WiFi"],
                qr_data=wifi_qr_payload(ssid, psk),
                qr_side="right",
            )
        else:
            display_client.show_onboarding(
                step=2,
                digit="2",
                lines=["setup", "node"],
                qr_data=setup_url(ip),
                qr_side="left",
            )
        if self._last_step != step:
            log.info(
                "onboarding step=%s board=%s stations=%s",
                step,
                board_digit(),
                len(stations),
            )
            self._last_step = step

    def reset_latch(self) -> None:
        """Force next OLED tick to re-push (does not clear wizard state)."""
        self._seen_sta = False
        self._last_step = None

    def reset_wizard(self) -> None:
        """Demo/full onboarding reset: clear latch and wizard file."""
        self.reset_latch()
        self.wizard.reset()
