#!/usr/bin/env python3
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Authenticated management HTTP API (stdlib only).

Endpoints:
  GET  /api/v1/health        (no auth)   liveness
  GET  /status               (no auth)   Amaru watch dashboard HTML
  GET  /api/v1/watch/node    (no auth)   read-only tip/host for /status
  GET  /api/v1/status        (api auth)  identity + state
  GET  /api/v1/about         (api auth)  attribution / licence
  GET  /api/v1/peers         (api auth)  discovery snapshot
  GET  /api/v1/associations  (api auth)  local hostapd stations
  GET  /api/v1/association/resolve (api auth) cluster MAC -> serving RockPi
  POST /api/v1/setup/claim   (api auth)  become coordinator + announce
  POST /api/v1/reset         (api auth)  demo reset (not project cleanup)
  GET  /api/v1/peer/hello    (peer auth) peer identity exchange
  GET  /api/v1/peer/association/lookup (peer auth) local MAC lookup
  GET  /api/v1/wizard/node/status  (setup session) Amaru catalog + cluster status
  POST /api/v1/wizard/node/*       (setup session) install/bootstrap/env/service/…
  GET  /api/v1/peer/node/*   (peer auth) adopted-board Amaru ops + snapshot bundle
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
from urllib.parse import parse_qs, urlparse

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
from cot_r_mgmt import amaru as amarumod  # noqa: E402
from cot_r_mgmt.association import AssociationTracker  # noqa: E402
from cot_r_mgmt.coordinator import CoordinatorService  # noqa: E402
from cot_r_mgmt.node_oled import NodeOled  # noqa: E402
from cot_r_mgmt.onboarding import OnboardingDisplay, board_digit  # noqa: E402
from cot_r_mgmt import peers as peersmod  # noqa: E402
from cot_r_mgmt.state import StateStore  # noqa: E402
from cot_r_mgmt.wizard import WizardStore  # noqa: E402

SETUP_HTML = Path(__file__).resolve().parent / "static" / "setup.html"
STATUS_HTML = Path(__file__).resolve().parent / "static" / "status.html"

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
        self.node_oled = NodeOled(wizard=self.wizard)
        self.associations = AssociationTracker(
            device_id=self.device_id,
            device_label=self.device_label,
            peer_token=self.peer_token,
        )
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


def _http_download(
    url: str,
    dest: Path,
    *,
    token: Optional[str],
    timeout: float = 600.0,
) -> None:
    import urllib.request

    headers = {"User-Agent": "cot-r-mgmt-peer/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    with urllib.request.urlopen(req, timeout=timeout) as resp, partial.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    partial.replace(dest)


def _file_response(
    handler: BaseHTTPRequestHandler,
    path: Path,
    *,
    content_type: str = "application/octet-stream",
    download_name: Optional[str] = None,
) -> None:
    if not path.is_file():
        _json_response(handler, 404, {"ok": False, "error": "file not found"})
        return
    size = path.stat().st_size
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(size))
    handler.send_header("Cache-Control", "no-store")
    if download_name:
        handler.send_header(
            "Content-Disposition", f'attachment; filename="{download_name}"'
        )
    handler.end_headers()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                handler.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        # Peer aborted a large snapshot/deb download; not a server bug.
        return


def _snapshot_stream_response(handler: BaseHTTPRequestHandler) -> None:
    """Stream live Amaru DB folders as uncompressed tar (no on-disk archive)."""
    size_info = amarumod.snapshot_size()
    if not size_info.get("ok"):
        _json_response(handler, 409, size_info)
        return
    expect = int(size_info.get("bytes") or 0)
    try:
        proc = amarumod.open_snapshot_stream_out()
    except OSError as e:
        _json_response(handler, 500, {"ok": False, "error": str(e)})
        return
    assert proc.stdout is not None
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-tar")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Cot-R-Snapshot-Mode", "stream")
    if expect > 0:
        handler.send_header("X-Cot-R-Expect-Bytes", str(expect))
    handler.send_header(
        "Content-Disposition", 'attachment; filename="amaru-db-stream.tar"'
    )
    # Chunked/EOF framing: size is approximate (tar headers add a little).
    handler.end_headers()
    err_tail = b""
    try:
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            handler.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        try:
            proc.kill()
        except OSError:
            pass
        return
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            if proc.stderr is not None:
                err_tail = proc.stderr.read() or b""
                proc.stderr.close()
        except OSError:
            err_tail = b""
        try:
            proc.wait(timeout=30)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except OSError:
                pass
    if proc.returncode not in (0, None):
        log.warning(
            "snapshot-stream-out exit %s: %s",
            proc.returncode,
            err_tail.decode("utf-8", errors="replace")[:500],
        )


def _setup_page_html() -> str:
    try:
        return SETUP_HTML.read_text(encoding="utf-8")
    except OSError:
        return (
            "<!DOCTYPE html><html><body><h1>Setup UI missing</h1>"
            "<p>static/setup.html not installed. Re-run prepare/update.</p></body></html>"
        )


def _status_page_html() -> str:
    try:
        return STATUS_HTML.read_text(encoding="utf-8")
    except OSError:
        return (
            "<!DOCTYPE html><html><body><h1>Status UI missing</h1>"
            "<p>static/status.html not installed. Re-run prepare/update.</p></body></html>"
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


def _drain_body(handler: BaseHTTPRequestHandler, limit: int = 64 * 1024) -> None:
    """Consume request body so keep-alive does not prepend leftovers to the next request."""
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return
    to_read = min(length, limit)
    while to_read > 0:
        chunk = handler.rfile.read(min(65536, to_read))
        if not chunk:
            break
        to_read -= len(chunk)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - " + fmt, self.address_string(), *args)

    def _require_api_auth(self) -> bool:
        assert CTX is not None
        provided = authmod.extract_bearer(self.headers.get("Authorization"))
        if authmod.token_ok(provided, CTX.token):
            return True
        if self.command in ("POST", "PUT", "PATCH"):
            _drain_body(self)
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})
        return False

    def _require_peer_auth(self) -> bool:
        assert CTX is not None
        if not CTX.peer_token:
            if self.command in ("POST", "PUT", "PATCH"):
                _drain_body(self)
            _json_response(self, 503, {"ok": False, "error": "peer token not configured"})
            return False
        provided = authmod.extract_bearer(self.headers.get("Authorization"))
        if authmod.token_ok(provided, CTX.peer_token):
            return True
        if self.command in ("POST", "PUT", "PATCH"):
            _drain_body(self)
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})
        return False

    def _require_wizard_session(self) -> bool:
        assert CTX is not None
        sid = self.headers.get("X-Setup-Session")
        if CTX.wizard.session_ok(sid):
            return True
        if self.command in ("POST", "PUT", "PATCH"):
            _drain_body(self)
        _json_response(self, 401, {"ok": False, "error": "setup session required"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        assert CTX is not None
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path in ("/setup", "/"):
            _html_response(self, 200, _setup_page_html())
            return
        if path == "/status":
            # Public watch dashboard (no wizard session).
            _html_response(self, 200, _status_page_html())
            return
        if path == "/api/v1/watch/node":
            # Read-only Amaru/host projection for phones scanning the OLED QR.
            try:
                payload = CTX.node_oled.watch_snapshot()
            except Exception as e:  # noqa: BLE001
                _json_response(self, 500, {"ok": False, "error": str(e)})
                return
            payload["device_id"] = CTX.device_id
            payload["device_label"] = CTX.device_label
            payload["board_digit"] = board_digit()
            payload["mgmt_ipv4"] = peersmod.local_mgmt_ipv4()
            _json_response(self, 200, payload)
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
            local_wiz = CTX.wizard.snapshot()
            my_id = CTX.device_id
            local_adopted = {
                str(p.get("addr") or "")
                for p in (local_wiz.get("adopted_peers") or [])
                if p.get("addr")
            }
            seen_addrs: set = set()
            candidates = []
            for entry in found:
                if not entry.get("reachable"):
                    continue
                hello = entry.get("hello") or {}
                addr = str(entry.get("addr") or "")
                if not addr:
                    continue
                seen_addrs.add(addr)
                if not hello.get("ok"):
                    candidates.append(
                        {
                            "addr": addr,
                            "device_id": None,
                            "device_label": None,
                            "board_digit": None,
                            "ui_step": None,
                            "status": "error",
                            "can_adopt": False,
                            "can_free": False,
                            "error": str(
                                hello.get("error")
                                or "peer hello failed (sync /etc/cot-r/peer.token)"
                            ),
                        }
                    )
                    continue
                wiz = hello.get("wizard") or {}
                role = str(wiz.get("role") or "solo")
                controlled_by_id = str(wiz.get("controlled_by_id") or "")
                controlled_by = str(wiz.get("controlled_by") or "")
                ui_step = int(wiz.get("ui_step") or 1)
                confirmed = bool(wiz.get("confirmed"))
                my_digit = board_digit()
                we_control = bool(
                    addr in local_adopted
                    or (
                        role == "adopted"
                        and controlled_by_id
                        and controlled_by_id == my_id
                    )
                    or (
                        role == "adopted"
                        and controlled_by
                        and controlled_by == my_digit
                    )
                )
                if we_control:
                    status = "adopted"
                elif role == "adopted":
                    status = "busy"
                elif confirmed or ui_step >= 3:
                    status = "busy"
                else:
                    status = "available"
                candidates.append(
                    {
                        "addr": addr,
                        "device_id": hello.get("device_id"),
                        "device_label": hello.get("device_label"),
                        "board_digit": hello.get("board_digit"),
                        "ui_step": ui_step,
                        "mgmt_state": hello.get("state"),
                        "wizard_role": role,
                        "controlled_by": controlled_by or None,
                        "controlled_by_id": controlled_by_id or None,
                        "confirmed": confirmed,
                        "status": status,
                        "busy_reason": (
                            None
                            if status != "busy"
                            else (
                                f"adopted by node {controlled_by or '?'}"
                                if role == "adopted"
                                else (
                                    "already in Nodes (step ≥ 3 / confirmed)"
                                    if (confirmed or ui_step >= 3)
                                    else "busy"
                                )
                            )
                        ),
                        "can_adopt": status == "available",
                        "can_free": status == "adopted",
                    }
                )
            # Keep previously adopted peers visible even if briefly unreachable.
            for p in local_wiz.get("adopted_peers") or []:
                addr = str(p.get("addr") or "")
                if not addr or addr in seen_addrs:
                    continue
                candidates.append(
                    {
                        "addr": addr,
                        "device_id": p.get("device_id"),
                        "device_label": p.get("device_label"),
                        "board_digit": None,
                        "ui_step": 3,
                        "status": "adopted",
                        "can_adopt": False,
                        "can_free": True,
                        "error": "unreachable (still listed as adopted)",
                    }
                )
            candidates.sort(key=lambda c: str(c.get("addr") or ""))
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
        if path == "/api/v1/associations":
            if not self._require_api_auth():
                return
            CTX.associations.refresh()
            _json_response(self, 200, CTX.associations.snapshot())
            return
        if path == "/api/v1/association/resolve":
            if not self._require_api_auth():
                return
            mac = (query.get("mac") or [""])[0]
            CTX.associations.refresh()
            _json_response(self, 200, CTX.associations.resolve(mac))
            return
        if path == "/api/v1/peer/association/lookup":
            if not self._require_peer_auth():
                return
            mac = (query.get("mac") or [""])[0]
            CTX.associations.refresh()
            local = CTX.associations.lookup_local(mac)
            ip = peersmod.local_mgmt_ipv4()
            port = int(os.environ.get("COT_R_MGMT_PORT", "8080"))
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "found": local is not None,
                    "device_id": CTX.device_id,
                    "device_label": CTX.device_label,
                    "mgmt_ipv4": ip,
                    "setup_url": f"http://{ip}:{port}/setup" if ip else None,
                    "association": local,
                },
            )
            return
        if path == "/api/v1/wizard/node/status":
            if not self._require_wizard_session():
                return
            wiz = CTX.wizard.snapshot()
            body = amarumod.cluster_status(
                CTX.peer_token,
                list(wiz.get("adopted_peers") or []),
            )
            _json_response(self, 200, body)
            return
        if path == "/api/v1/wizard/node/env":
            if not self._require_wizard_session():
                return
            result = amarumod.env_get()
            code = 200 if result.get("ok") else 404
            _json_response(self, code, result)
            return
        if path == "/api/v1/peer/node/status":
            if not self._require_peer_auth():
                return
            st = amarumod.status()
            st["device_id"] = CTX.device_id
            st["device_label"] = CTX.device_label
            st["job"] = amarumod.job_snapshot()
            _json_response(self, 200, st)
            return
        if path in (
            "/api/v1/peer/node/snapshot/stream",
            "/api/v1/peer/node/snapshot/bundle",
        ):
            if not self._require_peer_auth():
                return
            # bundle kept as alias; both stream live folders (no on-disk tarball).
            _snapshot_stream_response(self)
            return
        if path == "/api/v1/peer/node/deb":
            if not self._require_peer_auth():
                return
            deb = amarumod.deb_path()
            if not deb.get("ok"):
                _json_response(self, 404, deb)
                return
            path_file = Path(str(deb.get("path") or ""))
            _file_response(
                self,
                path_file,
                content_type="application/vnd.debian.binary-package",
                download_name=path_file.name,
            )
            return
        if path == "/api/v1/peer/hello":
            if not self._require_peer_auth():
                return
            _json_response(self, 200, CTX.coord.hello_payload())
            return
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        assert CTX is not None
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/v1/wizard/node/env":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            vars_list = body.get("vars")
            if not isinstance(vars_list, list):
                _json_response(self, 400, {"ok": False, "error": "vars list required"})
                return
            result = amarumod.env_put(vars_list)
            code = 200 if result.get("ok") else 500
            _json_response(self, code, result)
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
            # Wizard adoption only — do not claim_local()/announce (that forces peers into
            # Waiting + "Waiting for setup" OLED and fights the step-3 gear screen).
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
        if path == "/api/v1/wizard/free":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            addr = str(body.get("addr") or "").strip()
            if not addr:
                _json_response(self, 400, {"ok": False, "error": "addr required"})
                return
            if not CTX.peer_token:
                _json_response(self, 503, {"ok": False, "error": "peer.token missing"})
                return
            resp = peersmod.http_json(
                "POST",
                peersmod.peer_url(addr, "/api/v1/peer/wizard/free"),
                body={
                    "controller_id": CTX.device_id,
                    "reason": "wizard_free",
                },
                token=CTX.peer_token,
                timeout=max(peersmod.HTTP_TIMEOUT, 5.0),
            )
            if resp.get("http_status") == 401:
                resp.setdefault(
                    "error",
                    "unauthorized (peer.token mismatch on that board)",
                )
            # Drop from local adopted list on success or unreachable (stale entry).
            freed_local = False
            if resp.get("ok") or resp.get("unreachable"):
                snap = CTX.wizard.remove_adopted_peer(addr)
                freed_local = True
            else:
                snap = CTX.wizard.snapshot()
            ok = bool(resp.get("ok") or (resp.get("unreachable") and freed_local))
            _json_response(
                self,
                200 if ok else 502,
                {
                    "ok": ok,
                    "addr": addr,
                    "peer": resp,
                    "wizard": snap,
                    "error": None if ok else (resp.get("error") or "free failed"),
                },
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
                # Stay out of peer/Waiting claim role so coordinator loop does not
                # overwrite OLED with "Waiting for setup".
                CTX.store.set_state("Initial", note=f"wizard_adopted_by={controller_id}")
                display_client.show_onboarding(
                    step=3,
                    digit="3",
                    lines=["setup"],
                    qr_data="",
                    qr_side="right",
                    controlled_by=controller_digit,
                    priority=70,
                    ttl_ms=60000,
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
        if path == "/api/v1/peer/wizard/free":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            assert body is not None
            controller_id = str(body.get("controller_id") or "")
            wiz = CTX.wizard.snapshot()
            # Only the adopting controller (or missing link) may free this board.
            if (
                wiz.get("role") == "adopted"
                and wiz.get("controlled_by_id")
                and controller_id
                and controller_id != wiz.get("controlled_by_id")
            ):
                _json_response(
                    self,
                    409,
                    {
                        "ok": False,
                        "error": "adopted by a different controller",
                        "controlled_by_id": wiz.get("controlled_by_id"),
                    },
                )
                return
            try:
                from cot_r_mgmt import ap_control

                snap = CTX.wizard.release_from_adoption()
                CTX.store.expire_to_initial(reason=str(body.get("reason") or "wizard_free"))
                ap_control.resume()
                CTX.onboarding.reset_latch()
                # Clear any leftover high-priority Waiting text immediately.
                display_client.show_text(
                    ["Cardano Rocks", "setup ready.", "join Wi‑Fi"],
                    priority=75,
                    ttl_ms=8000,
                )
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
        if path == "/api/v1/wizard/node/install":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            target = str((body or {}).get("target") or "local")
            if target not in ("", "local", "self"):
                result = amarumod.install_on_peer(CTX.peer_token, target)
                _json_response(self, 200 if result.get("ok") else 409, result)
                return
            version = (body or {}).get("version")
            result = amarumod.install(str(version) if version else None)
            _json_response(self, 200 if result.get("ok") else 409, result)
            return
        if path == "/api/v1/wizard/node/bootstrap":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            target = str((body or {}).get("target") or "local")
            if target not in ("", "local", "self"):
                _json_response(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": (
                            "Mithril bootstrap is local-only; select this board, "
                            "or use Copy snapshot for an adopted peer"
                        ),
                    },
                )
                return
            result = amarumod.bootstrap()
            _json_response(self, 200 if result.get("ok") else 409, result)
            return
        if path == "/api/v1/wizard/node/bootstrap/stop":
            if not self._require_wizard_session():
                return
            _read_json(self)
            result = amarumod.bootstrap_stop()
            _json_response(self, 200 if result.get("ok") else 500, result)
            return
        if path == "/api/v1/wizard/node/wipe":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            target = str((body or {}).get("target") or "local")
            if target not in ("", "local", "self"):
                result = amarumod.peer_wipe(CTX.peer_token, target)
                _json_response(self, 200 if result.get("ok") else 500, result)
                return
            result = amarumod.wipe()
            _json_response(self, 200 if result.get("ok") else 500, result)
            return
        if path == "/api/v1/wizard/node/service":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            target = str((body or {}).get("target") or "local")
            action = str((body or {}).get("action") or "")
            if target not in ("", "local", "self"):
                result = amarumod.peer_service(CTX.peer_token, target, action)
                _json_response(self, 200 if result.get("ok") else 400, result)
                return
            result = amarumod.service(action)
            _json_response(self, 200 if result.get("ok") else 400, result)
            return
        if path == "/api/v1/wizard/node/tune-catchup":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            restart = True
            if body is not None and "restart" in body:
                restart = bool(body.get("restart"))
            result = amarumod.tune_catchup(restart=restart)
            _json_response(self, 200 if result.get("ok") else 500, result)
            return
        if path == "/api/v1/wizard/node/autostart":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            enabled = bool((body or {}).get("enabled"))
            result = amarumod.set_autostart(enabled)
            _json_response(self, 200 if result.get("ok") else 500, result)
            return
        if path == "/api/v1/wizard/node/push-snapshot":
            if not self._require_wizard_session():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            addrs = (body or {}).get("addrs") or []
            if not isinstance(addrs, list) or not addrs:
                # Default: all adopted
                wiz = CTX.wizard.snapshot()
                addrs = [
                    str(p.get("addr"))
                    for p in (wiz.get("adopted_peers") or [])
                    if p.get("addr")
                ]
            addrs = [str(a) for a in addrs if a]
            if not addrs:
                _json_response(self, 400, {"ok": False, "error": "no adopted peers"})
                return
            result = amarumod.push_snapshot_job(CTX.peer_token, addrs)
            _json_response(self, 200 if result.get("ok") else 409, result)
            return
        if path == "/api/v1/peer/node/install":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            master = str((body or {}).get("from_master") or "")
            st = amarumod.status()
            if st.get("installed"):
                _json_response(self, 200, {**st, "skipped": True})
                return
            if master:
                # Pull .deb from master cache over LAN.
                # Use Amaru cache (writable by cot-r); /run/cot-r is owned by cot-r-display.
                url = peersmod.peer_url(master, "/api/v1/peer/node/deb")
                cache = Path(os.environ.get("COT_R_AMARU_CACHE", "/var/cache/cot-r/amaru"))
                try:
                    cache.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
                dest = cache / "amaru-from-master.deb"
                try:
                    _http_download(url, dest, token=CTX.peer_token, timeout=600)
                except Exception as e:  # noqa: BLE001
                    _json_response(self, 502, {"ok": False, "error": f"deb pull failed: {e}"})
                    return
                result = amarumod.install_file(dest, background=False)
                _json_response(self, 200 if result.get("ok") else 500, result)
                return
            result = amarumod.install(background=False)
            _json_response(self, 200 if result.get("ok") else 500, result)
            return
        if path == "/api/v1/peer/node/snapshot/pull":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            master = str((body or {}).get("master") or "")
            url_path = str(
                (body or {}).get("url_path") or "/api/v1/peer/node/snapshot/stream"
            )
            if not master:
                _json_response(self, 400, {"ok": False, "error": "master required"})
                return
            url = peersmod.peer_url(master, url_path)
            # Drop leftover tarballs from the old push path.
            amarumod.snapshot_cache_clear()
            expect = (body or {}).get("expect_bytes")
            try:
                expect_n = int(expect) if expect is not None else 0
            except (TypeError, ValueError):
                expect_n = 0
            if expect_n > 0:
                import shutil

                home = Path("/var/lib/amaru")
                try:
                    free = shutil.disk_usage(str(home if home.exists() else "/")).free
                except OSError:
                    free = shutil.disk_usage("/").free
                # stream-in wipes local DBs first; count that reclaimable space.
                reclaim = 0
                try:
                    local_sz = amarumod.snapshot_size()
                    if local_sz.get("ok"):
                        reclaim = int(local_sz.get("bytes") or 0)
                except Exception:  # noqa: BLE001
                    reclaim = 0
                effective = free + reclaim
                need = int(expect_n * 1.15)
                if effective < need:
                    _json_response(
                        self,
                        507,
                        {
                            "ok": False,
                            "error": (
                                f"not enough free disk on peer: have {free // (1024 * 1024)} MiB free"
                                + (
                                    f" (+{reclaim // (1024 * 1024)} MiB reclaimable)"
                                    if reclaim
                                    else ""
                                )
                                + f", need ~{need // (1024 * 1024)} MiB for "
                                f"{expect_n // (1024 * 1024)} MiB streamed DB"
                            ),
                            "free_bytes": free,
                            "reclaim_bytes": reclaim,
                            "need_bytes": need,
                            "expect_bytes": expect_n,
                        },
                    )
                    return
            import urllib.request

            headers = {"User-Agent": "cot-r-mgmt-peer/1"}
            if CTX.peer_token:
                headers["Authorization"] = f"Bearer {CTX.peer_token}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=7200) as resp:
                    result = amarumod.run_snapshot_stream_in(resp)
            except Exception as e:  # noqa: BLE001
                _json_response(
                    self, 502, {"ok": False, "error": f"snapshot stream pull failed: {e}"}
                )
                return
            _json_response(self, 200 if result.get("ok") else 500, result)
            return
        if path == "/api/v1/peer/node/service":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            result = amarumod.service(str((body or {}).get("action") or ""))
            _json_response(self, 200 if result.get("ok") else 400, result)
            return
        if path == "/api/v1/peer/node/autostart":
            if not self._require_peer_auth():
                return
            body, err = _read_json(self)
            if err:
                _json_response(self, 400, {"ok": False, "error": err})
                return
            result = amarumod.set_autostart(bool((body or {}).get("enabled")))
            _json_response(self, 200 if result.get("ok") else 500, result)
            return
        if path == "/api/v1/peer/node/wipe":
            if not self._require_peer_auth():
                return
            _read_json(self)
            result = amarumod.wipe()
            _json_response(self, 200 if result.get("ok") else 500, result)
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
            # Release peers (coordinator claim and/or wizard-adopted list).
            peer_results: list = []
            reason = str(body.get("reason") or "api_reset")
            st_before = CTX.store.snapshot()
            wiz_before = CTX.wizard.snapshot()
            if st_before.get("role") == "coordinator":
                peer_results.extend(
                    CTX.coord.broadcast_release(reason=reason)
                )
            if CTX.peer_token:
                for p in wiz_before.get("adopted_peers") or []:
                    addr = str(p.get("addr") or "")
                    if not addr:
                        continue
                    resp = peersmod.http_json(
                        "POST",
                        peersmod.peer_url(addr, "/api/v1/peer/wizard/free"),
                        body={"controller_id": CTX.device_id, "reason": reason},
                        token=CTX.peer_token,
                        timeout=max(peersmod.HTTP_TIMEOUT, 5.0),
                    )
                    peer_results.append({"addr": addr, "via": "wizard_free", **resp})
            result = CTX.store.demo_reset(reason=reason)
            op_id = result.get("operation_id")
            try:
                from cot_r_mgmt import ap_control

                ap_control.resume()
            except Exception:  # noqa: BLE001
                pass
            CTX.onboarding.reset_wizard()
            CTX.node_oled.reset_peaks()
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
    CTX.node_oled.start()
    CTX.associations.start()

    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.daemon_threads = True

    def _stop(*_a):
        log.info("shutdown requested")
        CTX.associations.stop()
        CTX.node_oled.stop()
        CTX.onboarding.stop()
        CTX.coord.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("listening on http://%s:%s api=/%s token_file=%s", bind, port, API_VERSION, authmod.TOKEN_FILE)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        CTX.associations.stop()
        CTX.node_oled.stop()
        CTX.onboarding.stop()
        CTX.coord.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
