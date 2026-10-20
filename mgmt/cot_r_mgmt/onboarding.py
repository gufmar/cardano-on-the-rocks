# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""AP onboarding OLED: (1) Wi-Fi QR then (2) setup-URL QR after first STA."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from cot_r_mgmt import display_client
from cot_r_mgmt import peers
from cot_r_mgmt.state import StateStore

log = logging.getLogger("cot_r_mgmt.onboarding")

ETC_DIR = Path(os.environ.get("COT_R_ETC_DIR", "/etc/cot-r"))
AP_PSK_FILE = Path(os.environ.get("COT_R_AP_PSK_FILE", str(ETC_DIR / "ap.psk")))
AP_ENV = ETC_DIR / "ap.env"
AP_ENABLED_FLAG = Path(os.environ.get("COT_R_STATE_DIR", "/var/lib/cot-r")) / "ap-enabled"
POLL_SECONDS = float(os.environ.get("COT_R_ONBOARD_POLL", "2.0"))
WLAN_IF = os.environ.get("COT_R_WLAN_IF", "wlan0")
HOSTAPD_CLI = os.environ.get("COT_R_HOSTAPD_CLI", "/usr/sbin/hostapd_cli")
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
    # Compact local setup entry (HTML stub until full UI lands).
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
    """1|2|3 from device.env / reserved IP."""
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


def list_stations() -> List[str]:
    """MACs associated to the local AP (empty if AP down / no ctrl)."""
    if not Path(HOSTAPD_CLI).is_file() and not __import__("shutil").which("hostapd_cli"):
        return []
    cli = HOSTAPD_CLI if Path(HOSTAPD_CLI).is_file() else "hostapd_cli"
    try:
        proc = subprocess.run(
            [cli, "-i", WLAN_IF, "list_sta"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("hostapd_cli failed: %s", e)
        return []
    if proc.returncode != 0:
        return []
    macs: List[str] = []
    for line in (proc.stdout or "").splitlines():
        mac = line.strip().lower()
        if len(mac) == 17 and mac.count(":") == 5:
            macs.append(mac)
    return macs


def ap_is_enabled() -> bool:
    return AP_ENABLED_FLAG.is_file()


class OnboardingDisplay:
    """Background driver for the two-step OLED onboarding story."""

    def __init__(
        self,
        *,
        store: StateStore,
        should_run: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.store = store
        self._should_run = should_run or self._default_should_run
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_step: Optional[int] = None
        self._seen_sta = False

    def _default_should_run(self) -> bool:
        if not ap_is_enabled():
            return False
        st = self.store.snapshot()
        # Waiting peers have hostapd stopped; do not advertise join QR.
        if st.get("role") == "peer" and st.get("device_state") == "Waiting":
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
        # Latch: once a phone joined, stay on step 2 until demo reset / AP disable.
        # Digits 1/2 are the onboarding sequence (not the board index).
        step = 2 if self._seen_sta else 1

        if step == 1:
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
                "onboarding step=%s board=%s stations=%s url=%s",
                step,
                board_digit(),
                len(stations),
                setup_url(ip) if step == 2 else "wifi-qr",
            )
            self._last_step = step

    def reset_latch(self) -> None:
        """Call on demo reset so step 1 returns."""
        self._seen_sta = False
        self._last_step = None
