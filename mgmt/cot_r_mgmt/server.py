#!/usr/bin/env python3
# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Authenticated management HTTP API (stdlib only).

Endpoints:
  GET  /api/v1/health   (no auth)  liveness
  GET  /api/v1/status   (auth)     identity + state
  GET  /api/v1/about    (auth)     attribution / licence
  POST /api/v1/reset    (auth)     demo reset (not project cleanup)

Environment:
  COT_R_MGMT_BIND=0.0.0.0
  COT_R_MGMT_PORT=8080
  COT_R_API_TOKEN_FILE=/etc/cot-r/api.token
  COT_R_DEPLOY_INFO=/var/lib/cot-r/deploy.json
"""

from __future__ import annotations

import json
import logging
import os
import signal
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
from cot_r_mgmt.state import StateStore  # noqa: E402

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
        # If we just created token as non-root it may fail; prepare should create it.
        self.token = authmod.load_token()
        log.info(
            "boot device_id=%s label=%s boot_id=%s state=%s",
            self.device_id,
            self.device_label,
            self.boot_id,
            self.store.snapshot().get("device_state"),
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

    def _require_auth(self) -> bool:
        assert CTX is not None
        provided = authmod.extract_bearer(self.headers.get("Authorization"))
        if authmod.token_ok(provided, CTX.token):
            return True
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        assert CTX is not None
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/v1/health":
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "api": API_VERSION,
                    "version": __version__,
                    "boot_id": CTX.boot_id,
                },
            )
            return
        if path == "/api/v1/status":
            if not self._require_auth():
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
                    "session_id": st.get("session_id"),
                    "config_generation": st.get("config_generation"),
                    "coordinator_id": st.get("coordinator_id"),
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
            if not self._require_auth():
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
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        assert CTX is not None
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/v1/reset":
            if not self._require_auth():
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
            # Acknowledge before disruptive work where practical: build response after
            # durable state write (fast), then notify display asynchronously.
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
                },
            )
            threading.Thread(target=display_client.show_reset_notice, daemon=True).start()
            return
        _json_response(self, 404, {"ok": False, "error": "not found"})


def main() -> int:
    global CTX
    bind = os.environ.get("COT_R_MGMT_BIND", "0.0.0.0")
    port = int(os.environ.get("COT_R_MGMT_PORT", "8080"))
    CTX = AppContext()
    if not CTX.token:
        log.error("API token missing at %s; run sudo ./scripts/cot-r prepare", authmod.TOKEN_FILE)
        return 1

    display_client.show_text(
        ["Cardano Rocks", "default setup ready."],
        priority=50,
        ttl_ms=8000,
    )

    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.daemon_threads = True

    def _stop(*_a):
        log.info("shutdown requested")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("listening on http://%s:%s api=/%s token_file=%s", bind, port, API_VERSION, authmod.TOKEN_FILE)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
