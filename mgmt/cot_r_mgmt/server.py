#!/usr/bin/env python3
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Authenticated management HTTP API (stdlib only).

Endpoints:
  GET  /api/v1/health        (no auth)   liveness
  GET  /api/v1/status        (api auth)  identity + state
  GET  /api/v1/about         (api auth)  attribution / licence
  GET  /api/v1/peers         (api auth)  discovery snapshot
  POST /api/v1/setup/claim   (api auth)  become coordinator + announce
  POST /api/v1/reset         (api auth)  demo reset (not project cleanup)
  GET  /api/v1/peer/hello    (peer auth) peer identity exchange
  POST /api/v1/peer/claim    (peer auth) accept coordinator claim
  POST /api/v1/peer/heartbeat(peer auth) refresh peer lease
  POST /api/v1/peer/release  (peer auth) session ended / leave Waiting

Environment:
  COT_R_MGMT_BIND=0.0.0.0
  COT_R_MGMT_PORT=8080
  COT_R_API_TOKEN_FILE=/etc/cot-r/api.token
  COT_R_PEER_TOKEN_FILE=/etc/cot-r/peer.token
  COT_R_DEPLOY_INFO=/var/lib/cot-r/deploy.json
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from cot_r_mgmt import API_VERSION, SOURCE_URL, __version__  # noqa: E402
from cot_r_mgmt import auth as authmod  # noqa: E402
from cot_r_mgmt import display_client  # noqa: E402
from cot_r_mgmt import identity  # noqa: E402
from cot_r_mgmt import internet as internetmod  # noqa: E402
from cot_r_mgmt import routeros as ros  # noqa: E402
from cot_r_mgmt.coordinator import CoordinatorService  # noqa: E402
from cot_r_mgmt.onboarding import OnboardingDisplay, board_digit  # noqa: E402
from cot_r_mgmt import peers as peersmod  # noqa: E402
from cot_r_mgmt.state import StateStore  # noqa: E402
from cot_r_mgmt.wizard import WizardStore  # noqa: E402

SETUP_HTML = Path(__file__).resolve().parent / "static" / "setup.html"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("cot_r_mgmt.server")

DEPLOY_INFO = Path(os.environ.get("COT_R_DEPLOY_INFO", "/var/lib/cot-r/deploy.json"))


def _load_deploy_info() -> Dict[str, Any]:
    if not DEPLOY_INFO.is_file():
        return {}
    try:
        data = json.loads(DEPLOY_INFO.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class AppContext:
    def __init__(self) -> None:
        self.device_id = identity.ensure_device_id()
        self.device_label = identity.device_label()
        self.boot_id = identity.new_boot_id()
        self.store = StateStore()
        self.token = authmod.load_token() or authmod.ensure_token()
        self.token = authmod.load_token()
        self.peer_token = authmod.load_peer_token()
        self.wizard = WizardStore()
        self.coord = CoordinatorService(
            store=self.store,
            device_id=self.device_id,
            device_label=self.device_label,
            boot_id=self.boot_id,
            peer_token=self.peer_token,
            wizard=self.wizard,
        )
        self.onboarding = OnboardingDisplay(store=self.store, wizard=self.wizard)
        log.info(
            "boot device_id=%s label=%s boot_id=%s state=%s peer_token=%s",
            self.device_id,
            self.device_label,
            self.boot_id,
            self.store.snapshot().get("device_state"),
            "yes" if self.peer_token else "missing",
        )


CTX: Optional[AppContext] = None


def _json_response(handler: BaseHTTPRequestHandler, code: int, body: Dict[str, Any]) -> None:
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _html_response(handler: BaseHTTPRequestHandler, code: int, html: str) -> None:
    raw = html.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _setup_page_html() -> str:
    try:
        return SETUP_HTML.read_text(encoding="utf-8")
    except OSError:
        return (
            "<!DOCTYPE html><html><body><h1>Setup UI missing</h1>"
            "<p>static/setup.html not installed. Re-run prepare/update.</p></body></html>"
        )


def _read_json(handler: BaseHTTPRequestHandler, limit: int = 64 * 1024) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length > limit:
        return None, "body too large"
    if length <= 0:
        return {}, None
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, "invalid json"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, "json object required"
    return data, None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - " + fmt, self.address_string(), *args)

    def _require_api_auth(self) -> bool:
        assert CTX is not None
        provided = authmod.extract_bearer(self.headers.get("Authorization"))
        if authmod.token_ok(provided, CTX.token):
            return True
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})
        return False

    def _require_peer_auth(self) -> bool:
        assert CTX is not None
        if not CTX.peer_token:
            _json_response(self, 503, {"ok": False, "error": "peer token not configured"})
            return False
        provided = authmod.extract_bearer(self.headers.get("Authorization"))
        if authmod.token_ok(provided, CTX.peer_token):
            return True
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})
        return False

    def _require_wizard_session(self) -> bool:
        assert CTX is not None
        sid = self.headers.get("X-Setup-Session")
        if CTX.wizard.session_ok(sid):
            return True
        _json_response(self, 401, {"ok": False, "error": "setup session required"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        assert CTX is not None
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/setup", "/"):
            _html_response(self, 200, _setup_page_html())
            return
        if path == "/api/v1/wizard/status":
            wiz = CTX.wizard.snapshot()
            ip = peersmod.local_mgmt_ipv4()
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "device_id": CTX.device_id,
                    "device_label": CTX.device_label,
                    "board_digit": board_digit(),
                    "mgmt_ipv4": ip,
                    "confirmed": wiz.get("confirmed"),
                    "session_id": wiz.get("session_id") if wiz.get("confirmed") else None,
                    "ui_step": wiz.get("ui_step"),
                    "role": wiz.get("role"),
                    "internet_ok": wiz.get("internet_ok"),
                    "internet_skipped": wiz.get("internet_skipped"),
                    "uplink_status": wiz.get("uplink_status"),
                    "uplink_ssid": wiz.get("uplink_ssid"),
                    "ssh": ros.ssh_ready(),
                },
            )
            return
        if path == "/api/v1/wizard/internet":
            # Allow probe before confirm (welcome page) and after.
            probe = internetmod.probe()
            CTX.wizard.set_uplink(
                status="up" if probe.get("ok") else "down",
                detail=str(probe.get("detail") or ""),
                internet_ok=bool(probe.get("ok")),
            )
            _json_response(self, 200, {"ok": bool(probe.get("ok")), **probe})
            return
        if path == "/api/v1/wizard/peers":
            if not self._require_wizard_session():
                return
            found = CTX.coord.discover()
            candidates = []
            for entry in found:
                if not entry.get("reachable"):
                    continue
                hello = entry.get("hello") or {}
                addr = entry.get("addr")
                # Reachable health but failed peer auth → still show, not adoptable.
                if not hello.get("ok"):
                    candidates.append(
                        {
                            "addr": addr,
                            "device_id": None,
                            "device_label": None,
                            "board_digit": None,
                            "ui_step": None,
                            "adoptable": False,
                            "error": str(
                                hello.get("error")
                                or "peer hello failed (sync /etc/cot-r/peer.token)"
                            ),
                        }
                    )
                    continue
                wiz = hello.get("wizard") or {}
                if wiz.get("confirmed"):
                    continue
                if int(wiz.get("ui_step") or 1) >= 3:
                    continue
                label = hello.get("device_label")
                digit = hello.get("board_digit")
                candidates.append(
                    {
                        "addr": addr,
                        "device_id": hello.get("device_id"),
                        "device_label": label,
                        "board_digit": digit,
                        "ui_step": wiz.get("ui_step", 1),
                        "mgmt_state": hello.get("state"),
                        "adoptable": True,
                    }
                )
            _json_response(self, 200, {"ok": True, "candidates": candidates})
            return
        if path == "/api/v1/health":
            st = CTX.store.snapshot()
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "api": API_VERSION,
                    "version": __version__,
                    "boot_id": CTX.boot_id,
                    "state": st.get("device_state"),
                    "role": st.get("role"),
                },
            )
            return
        if path == "/api/v1/status":
            if not self._require_api_auth():
                return
            st = CTX.store.snapshot()
            deploy = _load_deploy_info()
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "api": API_VERSION,
                    "version": __version__,
                    "source": SOURCE_URL,
                    "device_id": CTX.device_id,
                    "device_label": CTX.device_label,
                    "boot_id": CTX.boot_id,
                    "state": st.get("device_state"),
                    "role": st.get("role"),
                    "session_id": st.get("session_id"),
                    "config_generation": st.get("config_generation"),
                    "coordinator_id": st.get("coordinator_id"),
                    "lease_expires_at": st.get("lease_expires_at"),
                    "peer_acks": st.get("peer_acks"),
                    "updated_at": st.get("updated_at"),
                    "deploy": {
                        "commit": deploy.get("commit"),
                        "ref": deploy.get("ref"),
                        "deployed_at": deploy.get("deployed_at"),
                        "previous_commit": deploy.get("previous_commit"),
                    },
                },
            )
            return
        if path == "/api/v1/about":
            if not self._require_api_auth():
                return
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "name": "Cardano on the Rocks",
                    "version": __version__,
                    "licence": "AGPL-3.0-only",
                    "copyright": "Markus (gufmar)",
                    "source": SOURCE_URL,
                    "note": (
                        "Modified network-facing deployments must offer corresponding "
                        "source for the running version (AGPL section 13)."
                    ),
                },
            )
            return
        if path == "/api/v1/peers":
            if not self._require_api_auth():
                return
            found = CTX.coord.discover()
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "peers": found,
                    "peer_token_configured": bool(CTX.peer_token),
                },
            )
            return
        if path == "/api/v1/peer/hello":
            if not self._require_peer_auth():
                return
            _json_response(self, 200, CTX.coord.hello_payload())
            return
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        assert CTX is not None
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/v1/wizard/confirm":
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            snap = CTX.wizard.confirm(device_label=CTX.device_label)
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "session_id": snap.get("session_id"),
                    "ui_step": snap.get("ui_step"),
                    "device_id": CTX.device_id,
                    "device_label": CTX.device_label,
                    "board_digit": board_digit(),
                    "mgmt_ipv4": peersmod.local_mgmt_ipv4(),
                    "confirmed": True,
                },
            )
            return
        if path == "/api/v1/wizard/uplink/scan":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            CTX.wizard.set_uplink(status="scanning", detail="scan requested")
            result = ros.scan_ssids(duration=int((body or {}).get("duration") or 6))
            if not result.get("ok"):
                CTX.wizard.set_uplink(status="error", detail=str(result.get("error") or "scan failed"))
                _json_response(self, 502, result)
                return
            CTX.wizard.set_uplink(status="scanned", detail=f"{len(result.get('networks') or [])} networks")
            _json_response(self, 200, result)
            return
        if path == "/api/v1/wizard/uplink/connect":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            from cot_r_mgmt import uplink_creds as creds

            ssid_raw = body.get("ssid")
            psk = body.get("psk")
            use_stored = bool(body.get("use_stored_psk"))
            psk_s = str(psk) if psk not in (None, "") else None
            ssid_s, ssid_err = ros.sanitize_ssid(ssid_raw)
            if ssid_err or not ssid_s:
                _json_response(self, 400, {"ok": False, "error": ssid_err or "ssid required"})
                return
            if use_stored or (psk_s is None and creds.has_psk(ssid_s)):
                stored = creds.get_psk(ssid_s)
                if not stored:
                    _json_response(
                        self,
                        400,
                        {"ok": False, "error": "no stored password for this SSID; enter one"},
                    )
                    return
                psk_s = stored
            CTX.wizard.set_uplink(status="connecting", ssid=ssid_s, detail="connect requested")
            result = ros.connect_uplink(ssid=ssid_s, psk=psk_s)
            if not result.get("ok"):
                CTX.wizard.set_uplink(status="error", ssid=ssid_s, detail=str(result.get("error") or ""))
                _json_response(self, 502, result)
                return
            # Persist newly entered PSK (never echo it back).
            if psk not in (None, "") and not use_stored:
                try:
                    creds.save_psk(ssid_s, str(psk))
                except OSError as e:
                    log.warning("failed to store uplink psk: %s", e)
            elif psk_s is None:
                try:
                    creds.mark_open(ssid_s)
                except OSError:
                    pass
            safe = {
                "ok": True,
                "ssid": str(result.get("ssid") or ssid_s),
                "detail": "applied on mAP; probe internet",
                "psk_known": creds.has_psk(ssid_s),
            }
            CTX.wizard.set_uplink(
                status="connecting",
                ssid=safe["ssid"],
                detail=safe["detail"],
            )
            _json_response(self, 200, safe)
            return
        if path == "/api/v1/wizard/uplink/skip":
            if not self._require_wizard_session():
                return
            CTX.wizard.set_uplink(
                status="skipped",
                detail="user skipped internet",
                internet_ok=False,
                skipped=True,
            )
            _json_response(self, 200, {"ok": True, "skipped": True})
            return
        if path == "/api/v1/wizard/adopt":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            addrs = [str(a) for a in (body.get("addrs") or []) if a]
            if not CTX.peer_token:
                _json_response(self, 503, {"ok": False, "error": "peer.token missing"})
                return
            # Become coordinator for demo session, then ask peers to enter wizard step 3.
            CTX.coord.claim_local()
            digit = board_digit()
            adopted: list = []
            results: list = []
            for addr in addrs:
                resp = peersmod.http_json(
                    "POST",
                    peersmod.peer_url(addr, "/api/v1/peer/wizard/adopt"),
                    body={
                        "controller_id": CTX.device_id,
                        "controller_label": CTX.device_label,
                        "controller_digit": digit,
                    },
                    token=CTX.peer_token,
                    timeout=max(peersmod.HTTP_TIMEOUT, 5.0),
                )
                if resp.get("http_status") == 401:
                    resp.setdefault(
                        "error",
                        "unauthorized (peer.token mismatch on that board)",
                    )
                results.append({"addr": addr, **resp})
                if resp.get("ok"):
                    adopted.append(
                        {
                            "addr": addr,
                            "device_id": resp.get("device_id"),
                            "device_label": resp.get("device_label"),
                        }
                    )
            snap = CTX.wizard.become_controller(adopted=adopted)
            _json_response(
                self,
                200,
                {"ok": True, "results": results, "wizard": snap},
            )
            return
        if path == "/api/v1/peer/wizard/adopt":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            controller_id = str(body.get("controller_id") or "")
            controller_digit = str(body.get("controller_digit") or "?")
            if not controller_id:
                _json_response(self, 400, {"ok": False, "error": "controller_id required"})
                return
            # Accept peer claim shape via existing accept path if session provided,
            # otherwise just mark wizard adopted + standby AP.
            try:
                from cot_r_mgmt import ap_control

                snap = CTX.wizard.become_adopted(
                    controller_digit=controller_digit,
                    controller_id=controller_id,
                )
                ap_control.standby()
                CTX.store.set_state("Waiting", note=f"adopted_by={controller_id}")
                # Push OLED step 3 immediately (do not wait for onboarding poll).
                display_client.show_onboarding(
                    step=3,
                    digit="3",
                    lines=["setup"],
                    qr_data="",
                    qr_side="right",
                    controlled_by=controller_digit,
                )
                CTX.onboarding.reset_latch()
            except Exception as e:  # noqa: BLE001
                _json_response(self, 500, {"ok": False, "error": str(e)})
                return
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "device_id": CTX.device_id,
                    "device_label": CTX.device_label,
                    "board_digit": board_digit(),
                    "wizard": snap,
                },
            )
            return
        if path == "/api/v1/setup/claim":
            if not self._require_api_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            if not CTX.peer_token:
                _json_response(
                    self,
                    503,
                    {
                        "ok": False,
                        "error": "peer.token missing; copy shared peer token to all boards",
                    },
                )
                return
            result = CTX.coord.claim_local()
            snap = result["state"]
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "role": "coordinator",
                    "state": snap.get("device_state"),
                    "session_id": snap.get("session_id"),
                    "config_generation": snap.get("config_generation"),
                    "lease_expires_at": snap.get("lease_expires_at"),
                    "device_id": CTX.device_id,
                    "boot_id": CTX.boot_id,
                    "peer_acks": result.get("peer_acks"),
                },
            )
            return
        if path == "/api/v1/peer/claim":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            result = CTX.coord.handle_remote_claim(body)
            code = 200 if result.get("ok") else 409
            _json_response(self, code, result)
            return
        if path == "/api/v1/peer/heartbeat":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            result = CTX.coord.handle_heartbeat(body)
            code = 200 if result.get("ok") else 409
            _json_response(self, code, result)
            return
        if path == "/api/v1/peer/release":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            result = CTX.coord.handle_release(body)
            _json_response(self, 200, result)
            return
        if path == "/api/v1/reset":
            if not self._require_api_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            scope = str(body.get("scope") or "demo")
            if scope != "demo":
                _json_response(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "only scope=demo is supported here; use cot-r reset-project for system cleanup",
                    },
                )
                return
            # If we are coordinator, notify peers before clearing local session.
            peer_results: list = []
            st_before = CTX.store.snapshot()
            if st_before.get("role") == "coordinator":
                peer_results = CTX.coord.broadcast_release(
                    reason=str(body.get("reason") or "api_reset")
                )
            result = CTX.store.demo_reset(reason=str(body.get("reason") or "api_reset"))
            op_id = result.get("operation_id")
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "accepted": True,
                    "operation_id": op_id,
                    "scope": "demo",
                    "state": result.get("device_state"),
                    "config_generation": result.get("config_generation"),
                    "boot_id": CTX.boot_id,
                    "device_id": CTX.device_id,
                    "ended_session": result.get("ended_session"),
                    "peer_releases": peer_results,
                },
            )
            CTX.onboarding.reset_latch()
            threading.Thread(target=display_client.show_reset_notice, daemon=True).start()
            return
        _json_response(self, 404, {"ok": False, "error": "not found"})


def _lease_ipv4() -> str:
    """Best-effort management IPv4 for OLED splash (prefer br0, then end0)."""
    for ifname in ("br0", "end0"):
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr", "show", "dev", ifname],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                return parts[3].split("/", 1)[0]
    return "ip unavailable"


def main() -> int:
    global CTX
    bind = os.environ.get("COT_R_MGMT_BIND", "0.0.0.0")
    port = int(os.environ.get("COT_R_MGMT_PORT", "8080"))
    CTX = AppContext()
    if not CTX.token:
        log.error("API token missing at %s; run sudo ./scripts/cot-r prepare", authmod.TOKEN_FILE)
        return 1
    if not CTX.peer_token:
        log.warning(
            "peer token missing at %s; peer coordination disabled until prepared",
            authmod.PEER_TOKEN_FILE,
        )

    display_client.show_text(
        ["Cardano Rocks", "default setup ready.", _lease_ipv4()],
        priority=50,
        ttl_ms=8000,
    )
    CTX.coord.start()
    CTX.onboarding.start()

    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.daemon_threads = True

    def _stop(*_a):
        log.info("shutdown requested")
        CTX.onboarding.stop()
        CTX.coord.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("listening on http://%s:%s api=/%s token_file=%s", bind, port, API_VERSION, authmod.TOKEN_FILE)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        CTX.onboarding.stop()
        CTX.coord.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
