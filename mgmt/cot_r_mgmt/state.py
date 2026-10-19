# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Persisted management state machine."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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

DEFAULT_LEASE_SECONDS = int(os.environ.get("COT_R_COORD_LEASE_SECONDS", "45"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class MgmtState:
    schema: int = 2
    device_state: str = "Initial"
    session_id: Optional[str] = None
    config_generation: int = 0
    coordinator_id: Optional[str] = None
    coordinator_session: Optional[str] = None
    coordinator_boot_id: Optional[str] = None
    role: str = "none"  # none | coordinator | peer
    lease_expires_at: Optional[str] = None
    updated_at: str = field(default_factory=_utc_now)
    ended_sessions: List[Dict[str, Any]] = field(default_factory=list)
    peer_acks: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MgmtState":
        st = cls()
        st.schema = int(data.get("schema", 2))
        st.device_state = str(data.get("device_state", "Initial"))
        if st.device_state not in VALID_STATES:
            st.device_state = "Recovery"
        st.session_id = data.get("session_id")
        st.config_generation = int(data.get("config_generation", 0))
        st.coordinator_id = data.get("coordinator_id")
        st.coordinator_session = data.get("coordinator_session")
        st.coordinator_boot_id = data.get("coordinator_boot_id")
        st.role = str(data.get("role") or "none")
        if st.role not in ("none", "coordinator", "peer"):
            st.role = "none"
        st.lease_expires_at = data.get("lease_expires_at")
        st.updated_at = str(data.get("updated_at") or _utc_now())
        st.ended_sessions = list(data.get("ended_sessions") or [])[-20:]
        st.peer_acks = dict(data.get("peer_acks") or {})
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

    def _note(self, msg: str) -> None:
        self._state.notes.append(f"{_utc_now()} {msg}")
        self._state.notes = self._state.notes[-50:]

    def set_state(self, new_state: str, *, note: Optional[str] = None) -> Dict[str, Any]:
        if new_state not in VALID_STATES:
            raise ValueError(f"invalid state: {new_state}")
        with self._lock:
            self._state.device_state = new_state
            if note:
                self._note(note)
            self._save()
            return self._state.to_dict()

    def lease_valid(self) -> bool:
        with self._lock:
            exp = _parse_ts(self._state.lease_expires_at)
            if not exp:
                return False
            return datetime.now(timezone.utc) <= exp

    def become_coordinator(
        self,
        *,
        device_id: str,
        boot_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            sid = session_id or str(uuid.uuid4())
            self._state.role = "coordinator"
            self._state.device_state = "Configuring"
            self._state.session_id = sid
            self._state.coordinator_id = device_id
            self._state.coordinator_session = sid
            self._state.coordinator_boot_id = boot_id
            self._state.config_generation = int(self._state.config_generation) + 1
            self._state.lease_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._state.peer_acks = {}
            self._note(f"became_coordinator session={sid}")
            self._save()
            return self._state.to_dict()

    def refresh_coordinator_lease(self, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Dict[str, Any]:
        with self._lock:
            if self._state.role != "coordinator":
                raise ValueError("not coordinator")
            self._state.lease_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save()
            return self._state.to_dict()

    def record_peer_ack(self, peer_id: str, info: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._state.peer_acks[peer_id] = {**info, "acked_at": _utc_now()}
            self._save()
            return self._state.to_dict()

    def accept_peer_offer(
        self,
        *,
        coordinator_id: str,
        session_id: str,
        coordinator_boot_id: Optional[str],
        generation: int,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        local_device_id: str,
    ) -> Dict[str, Any]:
        """Accept a coordinator offer and enter Waiting. Raises ValueError on conflict."""
        with self._lock:
            if self.is_ended_session_unlocked(session_id):
                raise ValueError("session ended")
            # Split-brain: if we are coordinator with a different live session, lower device_id wins.
            if (
                self._state.role == "coordinator"
                and self._state.session_id
                and self._state.session_id != session_id
                and self.lease_valid_unlocked()
            ):
                if local_device_id < coordinator_id:
                    raise ValueError("local coordinator wins tie-break")
                # Yield: fall through and accept remote
                self._note(f"yield_coordinator to={coordinator_id}")

            self._state.role = "peer"
            self._state.device_state = "Waiting"
            self._state.session_id = session_id
            self._state.coordinator_id = coordinator_id
            self._state.coordinator_session = session_id
            self._state.coordinator_boot_id = coordinator_boot_id
            if generation > int(self._state.config_generation):
                self._state.config_generation = generation
            self._state.lease_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._note(f"accept_peer_offer coordinator={coordinator_id} session={session_id}")
            self._save()
            return self._state.to_dict()

    def refresh_peer_lease(self, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Dict[str, Any]:
        with self._lock:
            if self._state.role != "peer":
                raise ValueError("not peer")
            self._state.lease_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save()
            return self._state.to_dict()

    def lease_valid_unlocked(self) -> bool:
        exp = _parse_ts(self._state.lease_expires_at)
        if not exp:
            return False
        return datetime.now(timezone.utc) <= exp

    def is_ended_session_unlocked(self, session_id: str) -> bool:
        return any(e.get("session_id") == session_id for e in self._state.ended_sessions)

    def expire_to_initial(self, *, reason: str) -> Dict[str, Any]:
        with self._lock:
            self._state.role = "none"
            self._state.device_state = "Initial"
            self._state.session_id = None
            self._state.coordinator_id = None
            self._state.coordinator_session = None
            self._state.coordinator_boot_id = None
            self._state.lease_expires_at = None
            self._state.peer_acks = {}
            self._note(f"expire_to_initial reason={reason}")
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
            self._state.role = "none"
            self._state.session_id = None
            self._state.coordinator_id = None
            self._state.coordinator_session = None
            self._state.coordinator_boot_id = None
            self._state.lease_expires_at = None
            self._state.peer_acks = {}
            self._state.config_generation = int(self._state.config_generation) + 1
            self._note(f"demo_reset op={op_id} reason={reason}")
            self._save()
            result = self._state.to_dict()
            result["operation_id"] = op_id
            result["ended_session"] = ended
            return result

    def is_ended_session(self, session_id: str) -> bool:
        with self._lock:
            return self.is_ended_session_unlocked(session_id)

    def record_ended_session(
        self,
        *,
        session_id: str,
        generation: int = 0,
        reason: str = "released",
    ) -> None:
        with self._lock:
            if not session_id or self.is_ended_session_unlocked(session_id):
                return
            self._state.ended_sessions.append(
                {
                    "session_id": session_id,
                    "generation": int(generation),
                    "ended_at": _utc_now(),
                    "reason": reason,
                }
            )
            self._state.ended_sessions = self._state.ended_sessions[-20:]
            self._save()
