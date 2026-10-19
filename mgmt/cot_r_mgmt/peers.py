# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Peer discovery and authenticated HTTP calls over the LAN."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("cot_r_mgmt.peers")

ETC_DIR = Path(os.environ.get("COT_R_ETC_DIR", "/etc/cot-r"))
DEVICE_ENV = ETC_DIR / "device.env"
DEFAULT_PEERS = (
    "172.27.213.194",
    "172.27.213.195",
    "172.27.213.196",
)
DEFAULT_PORT = int(os.environ.get("COT_R_MGMT_PORT", "8080"))
HTTP_TIMEOUT = float(os.environ.get("COT_R_PEER_HTTP_TIMEOUT", "2.0"))


def _read_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def local_mgmt_ipv4() -> Optional[str]:
    import subprocess

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
    return None


def peer_addresses(*, exclude_self: bool = True) -> List[str]:
    env = _read_env(DEVICE_ENV)
    raw = env.get("COT_R_PEER_ADDRS") or os.environ.get("COT_R_PEER_ADDRS") or ""
    if raw:
        addrs = [a.strip() for a in raw.split(",") if a.strip()]
    else:
        addrs = list(DEFAULT_PEERS)
    if exclude_self:
        self_ip = local_mgmt_ipv4()
        reserved = env.get("COT_R_RESERVED_IPV4")
        addrs = [a for a in addrs if a not in {self_ip, reserved}]
    # unique preserve order
    seen = set()
    out: List[str] = []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def peer_url(addr: str, path: str, port: int = DEFAULT_PORT) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{addr}:{port}{path}"


def http_json(
    method: str,
    url: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    timeout: float = HTTP_TIMEOUT,
) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "cot-r-mgmt-peer/1"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {"ok": True, "http_status": resp.status}
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed.setdefault("http_status", resp.status)
                return parsed
            return {"ok": True, "data": parsed, "http_status": resp.status}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
        except (UnicodeError, json.JSONDecodeError):
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        parsed["ok"] = False
        parsed["http_status"] = e.code
        parsed.setdefault("error", e.reason)
        return parsed
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e), "unreachable": True}


def probe_health(addr: str) -> Dict[str, Any]:
    result = http_json("GET", peer_url(addr, "/api/v1/health"), timeout=min(HTTP_TIMEOUT, 1.5))
    result["addr"] = addr
    return result


def discover(peer_token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Probe reserved peers; optionally fetch hello with peer token."""
    found: List[Dict[str, Any]] = []
    for addr in peer_addresses():
        health = probe_health(addr)
        entry: Dict[str, Any] = {
            "addr": addr,
            "reachable": bool(health.get("ok")) and not health.get("unreachable"),
            "health": health,
        }
        if entry["reachable"] and peer_token:
            hello = http_json(
                "GET",
                peer_url(addr, "/api/v1/peer/hello"),
                token=peer_token,
            )
            entry["hello"] = hello
        found.append(entry)
    return found
