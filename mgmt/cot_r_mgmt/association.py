# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Local Wi-Fi association tracking for shared-LAN portal handoff.

On a bridged AP the mAP DHCP table does not say which RockPi served the phone.
Each board polls hostapd and exposes fresh association records so a common
portal entry can redirect to the serving node.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from cot_r_mgmt import peers

log = logging.getLogger("cot_r_mgmt.association")

WLAN_IF = os.environ.get("COT_R_WLAN_IF", "wlan0")
HOSTAPD_CLI = os.environ.get("COT_R_HOSTAPD_CLI", "/usr/sbin/hostapd_cli")
POLL_SECONDS = float(os.environ.get("COT_R_ASSOC_POLL", "2.0"))
# Keep departed STAs visible briefly so a portal hit right after roam still resolves.
STALE_SECONDS = float(os.environ.get("COT_R_ASSOC_STALE", "30.0"))

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def normalize_mac(value: str) -> Optional[str]:
    raw = (value or "").strip().lower().replace("-", ":")
    if _MAC_RE.match(raw):
        return raw
    # Compact 12-hex form
    hex_only = re.sub(r"[^0-9a-f]", "", raw)
    if len(hex_only) == 12:
        return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))
    return None


def list_stations() -> List[str]:
    cli = HOSTAPD_CLI if Path(HOSTAPD_CLI).is_file() else shutil.which("hostapd_cli")
    if not cli:
        return []
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
        mac = normalize_mac(line)
        if mac:
            macs.append(mac)
    return macs


@dataclass
class AssociationRecord:
    mac: str
    generation: int
    first_seen: float
    last_seen: float
    active: bool

    def to_dict(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        t = now if now is not None else time.time()
        age = max(0.0, t - self.last_seen)
        return {
            "mac": self.mac,
            "generation": self.generation,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "active": self.active,
            "age_seconds": round(age, 3),
            "fresh": self.active or age <= STALE_SECONDS,
        }


class AssociationTracker:
    """Poll hostapd and keep a generation-aware association table."""

    def __init__(
        self,
        *,
        device_id: str,
        device_label: str,
        peer_token: Optional[str] = None,
    ) -> None:
        self.device_id = device_id
        self.device_label = device_label
        self.peer_token = peer_token
        self._lock = threading.Lock()
        self._by_mac: Dict[str, AssociationRecord] = {}
        self._generation = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="cot-r-assoc", daemon=True
        )
        self._thread.start()
        log.info("association tracker started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _loop(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.refresh()
            except Exception:  # noqa: BLE001
                log.exception("association refresh failed")

    def refresh(self) -> None:
        now = time.time()
        present = set(list_stations())
        with self._lock:
            for mac in present:
                rec = self._by_mac.get(mac)
                if rec is None or not rec.active:
                    self._generation += 1
                    self._by_mac[mac] = AssociationRecord(
                        mac=mac,
                        generation=self._generation,
                        first_seen=now if rec is None else rec.first_seen,
                        last_seen=now,
                        active=True,
                    )
                else:
                    rec.last_seen = now
                    rec.active = True
            for mac, rec in list(self._by_mac.items()):
                if mac not in present:
                    rec.active = False
                    if now - rec.last_seen > STALE_SECONDS:
                        del self._by_mac[mac]

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            stations = [
                rec.to_dict(now=now)
                for rec in sorted(self._by_mac.values(), key=lambda r: r.last_seen, reverse=True)
                if rec.active or (now - rec.last_seen) <= STALE_SECONDS
            ]
            gen = self._generation
        return {
            "ok": True,
            "device_id": self.device_id,
            "device_label": self.device_label,
            "mgmt_ipv4": peers.local_mgmt_ipv4(),
            "wlan_if": WLAN_IF,
            "stale_seconds": STALE_SECONDS,
            "generation": gen,
            "stations": stations,
            "count": len(stations),
        }

    def lookup_local(self, mac: str) -> Optional[Dict[str, Any]]:
        norm = normalize_mac(mac)
        if not norm:
            return None
        now = time.time()
        with self._lock:
            rec = self._by_mac.get(norm)
            if rec is None:
                return None
            data = rec.to_dict(now=now)
            if not data["fresh"]:
                return None
            return data

    def resolve(self, mac: str) -> Dict[str, Any]:
        """Find which RockPi (local or peer) currently serves this MAC."""
        norm = normalize_mac(mac)
        if not norm:
            return {"ok": False, "error": "invalid mac", "mac": mac}

        candidates: List[Dict[str, Any]] = []
        local = self.lookup_local(norm)
        if local:
            candidates.append(
                {
                    "source": "local",
                    "device_id": self.device_id,
                    "device_label": self.device_label,
                    "mgmt_ipv4": peers.local_mgmt_ipv4(),
                    "setup_url": (
                        f"http://{peers.local_mgmt_ipv4()}:{peers.DEFAULT_PORT}/setup"
                        if peers.local_mgmt_ipv4()
                        else None
                    ),
                    "association": local,
                }
            )

        if self.peer_token:
            for addr in peers.peer_addresses():
                resp = peers.http_json(
                    "GET",
                    peers.peer_url(addr, f"/api/v1/peer/association/lookup?mac={norm}"),
                    token=self.peer_token,
                )
                if not resp.get("ok") or not resp.get("found"):
                    continue
                assoc = resp.get("association") or {}
                candidates.append(
                    {
                        "source": "peer",
                        "device_id": resp.get("device_id"),
                        "device_label": resp.get("device_label"),
                        "mgmt_ipv4": resp.get("mgmt_ipv4") or addr,
                        "setup_url": resp.get("setup_url")
                        or f"http://{addr}:{peers.DEFAULT_PORT}/setup",
                        "association": assoc,
                    }
                )

        # Prefer active, then freshest last_seen.
        def _score(c: Dict[str, Any]) -> tuple:
            a = c.get("association") or {}
            return (0 if a.get("active") else 1, -(float(a.get("last_seen") or 0)))

        candidates.sort(key=_score)
        best = candidates[0] if candidates else None
        return {
            "ok": True,
            "mac": norm,
            "found": best is not None,
            "serving": best,
            "candidates": candidates,
        }
