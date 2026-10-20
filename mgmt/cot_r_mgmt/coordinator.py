# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Coordinator claim, peer announcements, lease refresh, and expiry."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional  # Any: optional WizardStore

from cot_r_mgmt import ap_control
from cot_r_mgmt import display_client
from cot_r_mgmt import peers
from cot_r_mgmt.state import DEFAULT_LEASE_SECONDS, StateStore

log = logging.getLogger("cot_r_mgmt.coordinator")

HEARTBEAT_INTERVAL = float(os.environ.get("COT_R_COORD_HEARTBEAT_INTERVAL", "15"))
LEASE_SECONDS = int(os.environ.get("COT_R_COORD_LEASE_SECONDS", str(DEFAULT_LEASE_SECONDS)))


class CoordinatorService:
    """Background peer coordination for one management process."""

    def __init__(
        self,
        *,
        store: StateStore,
        device_id: str,
        device_label: str,
        boot_id: str,
        peer_token: Optional[str],
        wizard: Any = None,
        on_waiting: Optional[Callable[[], None]] = None,
        on_initial: Optional[Callable[[], None]] = None,
    ) -> None:
        self.store = store
        self.device_id = device_id
        self.device_label = device_label
        self.boot_id = boot_id
        self.peer_token = peer_token
        self.wizard = wizard
        self._on_waiting = on_waiting or self._default_waiting
        self._on_initial = on_initial or self._default_initial
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ap_standby_active = False
        self._last_role: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cot-r-coord", daemon=True)
        self._thread.start()
        log.info("coordinator loop started interval=%.1fs lease=%ss", HEARTBEAT_INTERVAL, LEASE_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _default_waiting(self) -> None:
        if not self._ap_standby_active:
            if ap_control.standby():
                self._ap_standby_active = True
        display_client.show_waiting()

    def _default_initial(self) -> None:
        if self._ap_standby_active:
            if ap_control.resume():
                self._ap_standby_active = False
        display_client.show_text(
            ["Cardano Rocks", "default setup ready."],
            priority=50,
            ttl_ms=6000,
        )

    def claim_local(self) -> Dict[str, Any]:
        """Become coordinator from a local setup start; announce to peers."""
        snap = self.store.become_coordinator(
            device_id=self.device_id,
            boot_id=self.boot_id,
            lease_seconds=LEASE_SECONDS,
        )
        self._last_role = "coordinator"
        if self._ap_standby_active:
            ap_control.resume()
            self._ap_standby_active = False
        acks = self.announce_claim()
        display_client.show_text(
            ["Coordinator", self.device_label[:16], "setup active"],
            priority=70,
            ttl_ms=8000,
        )
        return {"state": snap, "peer_acks": acks}

    def announce_claim(self) -> List[Dict[str, Any]]:
        """POST claim to all reachable peers; record acknowledgements."""
        st = self.store.snapshot()
        if st.get("role") != "coordinator" or not self.peer_token:
            return []
        body = {
            "coordinator_id": self.device_id,
            "coordinator_label": self.device_label,
            "boot_id": self.boot_id,
            "session_id": st.get("session_id"),
            "config_generation": st.get("config_generation"),
            "lease_seconds": LEASE_SECONDS,
        }
        results: List[Dict[str, Any]] = []
        for addr in peers.peer_addresses():
            resp = peers.http_json(
                "POST",
                peers.peer_url(addr, "/api/v1/peer/claim"),
                body=body,
                token=self.peer_token,
            )
            entry = {"addr": addr, **resp}
            results.append(entry)
            if resp.get("ok") and resp.get("acked"):
                peer_id = str(resp.get("device_id") or addr)
                self.store.record_peer_ack(
                    peer_id,
                    {
                        "addr": addr,
                        "device_label": resp.get("device_label"),
                        "boot_id": resp.get("boot_id"),
                        "state": resp.get("state"),
                    },
                )
        return results

    def handle_remote_claim(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Accept or reject a claim from another device."""
        coordinator_id = str(body.get("coordinator_id") or "")
        session_id = str(body.get("session_id") or "")
        if not coordinator_id or not session_id:
            return {"ok": False, "error": "coordinator_id and session_id required", "acked": False}
        if self.store.is_ended_session(session_id):
            return {"ok": False, "error": "session ended", "acked": False, "ended": True}

        lease_seconds = int(body.get("lease_seconds") or LEASE_SECONDS)
        generation = int(body.get("config_generation") or 0)
        try:
            snap = self.store.accept_peer_offer(
                coordinator_id=coordinator_id,
                session_id=session_id,
                coordinator_boot_id=body.get("boot_id"),
                generation=generation,
                lease_seconds=lease_seconds,
                local_device_id=self.device_id,
            )
        except ValueError as e:
            return {
                "ok": False,
                "error": str(e),
                "acked": False,
                "device_id": self.device_id,
                "state": self.store.snapshot().get("device_state"),
            }

        self._last_role = "peer"
        # If this board is already wizard-adopted, keep step-3 OLED.
        wiz = self.wizard.snapshot() if self.wizard is not None else {}
        if wiz.get("role") == "adopted" or int(wiz.get("ui_step") or 0) >= 3:
            pass
        else:
            self._on_waiting()
        return {
            "ok": True,
            "acked": True,
            "device_id": self.device_id,
            "device_label": self.device_label,
            "boot_id": self.boot_id,
            "state": snap.get("device_state"),
            "session_id": snap.get("session_id"),
            "role": snap.get("role"),
        }

    def handle_heartbeat(self, body: Dict[str, Any]) -> Dict[str, Any]:
        st = self.store.snapshot()
        session_id = str(body.get("session_id") or "")
        coordinator_id = str(body.get("coordinator_id") or "")
        if st.get("role") != "peer":
            return {"ok": False, "error": "not a peer", "acked": False}
        if session_id and session_id != st.get("session_id"):
            return {"ok": False, "error": "session mismatch", "acked": False}
        if coordinator_id and coordinator_id != st.get("coordinator_id"):
            return {"ok": False, "error": "coordinator mismatch", "acked": False}
        if self.store.is_ended_session(session_id or st.get("session_id") or ""):
            self.store.expire_to_initial(reason="ended_session")
            self._apply_initial()
            return {"ok": False, "error": "session ended", "acked": False, "ended": True}
        lease_seconds = int(body.get("lease_seconds") or LEASE_SECONDS)
        snap = self.store.refresh_peer_lease(lease_seconds=lease_seconds)
        return {
            "ok": True,
            "acked": True,
            "device_id": self.device_id,
            "boot_id": self.boot_id,
            "state": snap.get("device_state"),
            "lease_expires_at": snap.get("lease_expires_at"),
        }

    def handle_release(self, body: Dict[str, Any]) -> Dict[str, Any]:
        st = self.store.snapshot()
        session_id = str(body.get("session_id") or "")
        if session_id and st.get("session_id") and session_id != st.get("session_id"):
            return {"ok": True, "ignored": True, "reason": "different session"}
        reason = str(body.get("reason") or "coordinator_released")
        if session_id:
            self.store.record_ended_session(
                session_id=session_id,
                generation=int(body.get("config_generation") or st.get("config_generation") or 0),
                reason=reason,
            )
        self.store.expire_to_initial(reason=reason)
        if self.wizard is not None:
            try:
                self.wizard.release_from_adoption()
            except Exception:  # noqa: BLE001
                log.exception("wizard release_from_adoption failed")
        self._apply_initial()
        return {"ok": True, "acked": True, "state": "Initial"}

    def broadcast_release(self, *, reason: str = "demo_reset") -> List[Dict[str, Any]]:
        st = self.store.snapshot()
        session_id = st.get("session_id") or st.get("coordinator_session")
        if not self.peer_token or not session_id:
            return []
        body = {
            "coordinator_id": self.device_id,
            "session_id": session_id,
            "config_generation": st.get("config_generation"),
            "reason": reason,
        }
        results: List[Dict[str, Any]] = []
        for addr in peers.peer_addresses():
            resp = peers.http_json(
                "POST",
                peers.peer_url(addr, "/api/v1/peer/release"),
                body=body,
                token=self.peer_token,
            )
            results.append({"addr": addr, **resp})
        return results

    def hello_payload(self) -> Dict[str, Any]:
        st = self.store.snapshot()
        wiz = self.wizard.snapshot() if self.wizard is not None else {}
        from cot_r_mgmt.onboarding import board_digit

        return {
            "ok": True,
            "device_id": self.device_id,
            "device_label": self.device_label,
            "boot_id": self.boot_id,
            "board_digit": board_digit(),
            "state": st.get("device_state"),
            "role": st.get("role"),
            "session_id": st.get("session_id"),
            "config_generation": st.get("config_generation"),
            "coordinator_id": st.get("coordinator_id"),
            "lease_expires_at": st.get("lease_expires_at"),
            "mgmt_ipv4": peers.local_mgmt_ipv4(),
            "wizard": {
                "ui_step": wiz.get("ui_step"),
                "confirmed": wiz.get("confirmed"),
                "role": wiz.get("role"),
                "controlled_by": wiz.get("controlled_by"),
                "controlled_by_id": wiz.get("controlled_by_id"),
            },
        }

    def discover(self) -> List[Dict[str, Any]]:
        return peers.discover(self.peer_token)

    def _apply_initial(self) -> None:
        self._last_role = "none"
        self._on_initial()

    def _send_heartbeats(self) -> None:
        st = self.store.snapshot()
        if not self.peer_token:
            return
        body = {
            "coordinator_id": self.device_id,
            "boot_id": self.boot_id,
            "session_id": st.get("session_id"),
            "config_generation": st.get("config_generation"),
            "lease_seconds": LEASE_SECONDS,
        }
        for addr in peers.peer_addresses():
            resp = peers.http_json(
                "POST",
                peers.peer_url(addr, "/api/v1/peer/heartbeat"),
                body=body,
                token=self.peer_token,
            )
            if resp.get("ok") and resp.get("acked"):
                peer_id = str(resp.get("device_id") or addr)
                self.store.record_peer_ack(
                    peer_id,
                    {
                        "addr": addr,
                        "boot_id": resp.get("boot_id"),
                        "state": resp.get("state"),
                        "via": "heartbeat",
                    },
                )

    def _loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("coordinator tick failed")

    def _tick(self) -> None:
        st = self.store.snapshot()
        role = st.get("role") or "none"

        if role == "coordinator":
            if not self.store.lease_valid():
                log.warning("coordinator lease expired locally; refreshing")
            self.store.refresh_coordinator_lease(lease_seconds=LEASE_SECONDS)
            self._send_heartbeats()
            if self._last_role != "coordinator":
                self._last_role = "coordinator"
            return

        if role == "peer":
            # Wizard-adopted boards own the OLED (step 3 / controlled by); do not
            # paint the older "Waiting for setup" claim screen over them.
            if self.wizard is not None:
                wiz = self.wizard.snapshot()
                if wiz.get("role") == "adopted" or int(wiz.get("ui_step") or 0) >= 3:
                    self._last_role = "peer"
                    return
            if not self.store.lease_valid():
                log.info("peer lease expired; returning to Initial")
                self.store.expire_to_initial(reason="lease_expired")
                self._apply_initial()
                return
            if self._last_role != "peer":
                self._last_role = "peer"
                self._on_waiting()
            else:
                # Refresh waiting OLED periodically (ttl-based).
                display_client.show_waiting()
            return

        # role none / Initial
        if self._last_role == "peer" or self._ap_standby_active:
            self._apply_initial()
            self._last_role = "none"
        elif self._last_role != "none":
            self._last_role = "none"
