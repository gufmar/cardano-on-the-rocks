# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Persisted management state machine."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path(os.environ.get("COT_R_STATE_DIR", "/var/lib/cot-r"))
STATE_FILE = STATE_DIR / "mgmt-state.json"

VALID_STATES = (
    "Initial",
    "Waiting",
    "Configuring",
    "Ready",
    "Recovery",
    "Resetting",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class EndedSession:
    session_id: str
    generation: int
    ended_at: str
    reason: str = "reset"


@dataclass
class MgmtState:
    schema: int = 1
    device_state: str = "Initial"
    session_id: Optional[str] = None
    config_generation: int = 0
    coordinator_id: Optional[str] = None
    coordinator_session: Optional[str] = None
    updated_at: str = field(default_factory=_utc_now)
    ended_sessions: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MgmtState":
        st = cls()
        st.schema = int(data.get("schema", 1))
        st.device_state = str(data.get("device_state", "Initial"))
        if st.device_state not in VALID_STATES:
            st.device_state = "Recovery"
        st.session_id = data.get("session_id")
        st.config_generation = int(data.get("config_generation", 0))
        st.coordinator_id = data.get("coordinator_id")
        st.coordinator_session = data.get("coordinator_session")
        st.updated_at = str(data.get("updated_at") or _utc_now())
        st.ended_sessions = list(data.get("ended_sessions") or [])[-20:]
        st.notes = list(data.get("notes") or [])[-50:]
        return st


class StateStore:
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> MgmtState:
        if not self.path.is_file():
            return MgmtState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return MgmtState(device_state="Recovery", notes=["corrupt state replaced"])
            return MgmtState.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return MgmtState(device_state="Recovery", notes=["unreadable state replaced"])

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

    def set_state(self, new_state: str, *, note: Optional[str] = None) -> Dict[str, Any]:
        if new_state not in VALID_STATES:
            raise ValueError(f"invalid state: {new_state}")
        with self._lock:
            self._state.device_state = new_state
            if note:
                self._state.notes.append(f"{_utc_now()} {note}")
                self._state.notes = self._state.notes[-50:]
            self._save()
            return self._state.to_dict()

    def demo_reset(self, *, reason: str = "api_reset") -> Dict[str, Any]:
        """Clear demo session; keep durable identity elsewhere. Idempotent."""
        with self._lock:
            op_id = f"reset-{_utc_now().replace(':', '')}"
            ended = None
            if self._state.session_id:
                ended = {
                    "session_id": self._state.session_id,
                    "generation": self._state.config_generation,
                    "ended_at": _utc_now(),
                    "reason": reason,
                }
                self._state.ended_sessions.append(ended)
                self._state.ended_sessions = self._state.ended_sessions[-20:]
            self._state.device_state = "Initial"
            self._state.session_id = None
            self._state.coordinator_id = None
            self._state.coordinator_session = None
            # Keep config_generation monotonic so stale sessions cannot reclaim.
            self._state.config_generation = int(self._state.config_generation) + 1
            self._state.notes.append(f"{_utc_now()} demo_reset op={op_id} reason={reason}")
            self._state.notes = self._state.notes[-50:]
            self._save()
            result = self._state.to_dict()
            result["operation_id"] = op_id
            result["ended_session"] = ended
            return result

    def is_ended_session(self, session_id: str) -> bool:
        with self._lock:
            return any(e.get("session_id") == session_id for e in self._state.ended_sessions)
