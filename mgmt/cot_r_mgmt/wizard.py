# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Local setup wizard state (phone-driven; no mAP captive portal)."""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path(os.environ.get("COT_R_STATE_DIR", "/var/lib/cot-r"))
WIZARD_FILE = STATE_DIR / "wizard-state.json"

# OLED / UI steps
# 1 = Wi-Fi QR, 2 = setup URL QR, 3 = configuring (gear), …
STEP_WIFI_QR = 1
STEP_SETUP_QR = 2
STEP_CONFIGURING = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class WizardState:
    schema: int = 1
    # display / flow step (1..3+)
    ui_step: int = STEP_WIFI_QR
    # phone confirmed "about to configure"
    confirmed: bool = False
    session_id: Optional[str] = None
    # uplink
    internet_ok: Optional[bool] = None
    internet_skipped: bool = False
    uplink_ssid: Optional[str] = None
    uplink_status: str = "unknown"  # unknown|scanning|connecting|up|down|skipped|error
    uplink_detail: str = ""
    # adoption
    role: str = "solo"  # solo|controller|adopted
    controlled_by: Optional[str] = None  # board digit or label of controller
    controlled_by_id: Optional[str] = None
    adopted_peers: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WizardState":
        st = cls()
        st.schema = int(data.get("schema", 1))
        st.ui_step = int(data.get("ui_step", STEP_WIFI_QR))
        st.confirmed = bool(data.get("confirmed", False))
        st.session_id = data.get("session_id")
        st.internet_ok = data.get("internet_ok")
        st.internet_skipped = bool(data.get("internet_skipped", False))
        st.uplink_ssid = data.get("uplink_ssid")
        st.uplink_status = str(data.get("uplink_status") or "unknown")
        st.uplink_detail = str(data.get("uplink_detail") or "")
        st.role = str(data.get("role") or "solo")
        if st.role not in ("solo", "controller", "adopted"):
            st.role = "solo"
        st.controlled_by = data.get("controlled_by")
        st.controlled_by_id = data.get("controlled_by_id")
        st.adopted_peers = list(data.get("adopted_peers") or [])
        st.updated_at = str(data.get("updated_at") or _utc_now())
        return st


class WizardStore:
    def __init__(self, path: Path = WIZARD_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> WizardState:
        if not self.path.is_file():
            return WizardState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return WizardState()
            return WizardState.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return WizardState()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state.updated_at = _utc_now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o640)
        except OSError:
            pass

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def session_ok(self, session_id: Optional[str]) -> bool:
        with self._lock:
            if not self._state.confirmed or not self._state.session_id:
                return False
            a = str(session_id or "")
            b = str(self._state.session_id)
            if not a or len(a) != len(b):
                return False
            return secrets.compare_digest(a, b)

    def confirm(self, *, device_label: str) -> Dict[str, Any]:
        with self._lock:
            sid = secrets.token_urlsafe(24)
            self._state.confirmed = True
            self._state.session_id = sid
            self._state.ui_step = STEP_CONFIGURING
            self._state.role = "solo"
            self._state.controlled_by = None
            self._state.controlled_by_id = None
            self._save()
            out = self._state.to_dict()
            out["device_label"] = device_label
            return out

    def set_uplink(
        self,
        *,
        status: str,
        detail: str = "",
        ssid: Optional[str] = None,
        internet_ok: Optional[bool] = None,
        skipped: Optional[bool] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            self._state.uplink_status = status
            self._state.uplink_detail = detail
            if ssid is not None:
                self._state.uplink_ssid = ssid
            if internet_ok is not None:
                self._state.internet_ok = internet_ok
            if skipped is not None:
                self._state.internet_skipped = skipped
            self._save()
            return self._state.to_dict()

    def become_controller(self, *, adopted: List[Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            self._state.role = "controller"
            self._state.adopted_peers = adopted
            self._state.controlled_by = None
            self._state.controlled_by_id = None
            self._state.ui_step = STEP_CONFIGURING
            self._save()
            return self._state.to_dict()

    def become_adopted(self, *, controller_digit: str, controller_id: str) -> Dict[str, Any]:
        with self._lock:
            self._state.role = "adopted"
            self._state.controlled_by = controller_digit
            self._state.controlled_by_id = controller_id
            self._state.confirmed = True
            self._state.ui_step = STEP_CONFIGURING
            if not self._state.session_id:
                self._state.session_id = secrets.token_urlsafe(16)
            self._save()
            return self._state.to_dict()

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self._state = WizardState()
            self._save()
            return self._state.to_dict()
