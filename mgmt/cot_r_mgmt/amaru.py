# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Amaru node control via privileged helper (install / bootstrap / env / service)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from cot_r_mgmt import peers

log = logging.getLogger("cot_r_mgmt.amaru")

HELPER = Path(os.environ.get("COT_R_AMARU_HELPER", "/opt/cot-r/bin/cot-r-amaru-helper"))
RUN_DIR = Path(os.environ.get("COT_R_RUN_DIR", "/run/cot-r"))
CACHE_DIR = Path(os.environ.get("COT_R_AMARU_CACHE", "/var/cache/cot-r/amaru"))

CATALOG = [
    {
        "id": "amaru",
        "name": "Amaru",
        "lang": "Rust",
        "enabled": True,
        "summary": "Debian aarch64 package + systemd; Mithril mainnet bootstrap (~10 GiB).",
    },
    {
        "id": "dingo",
        "name": "Dingo",
        "lang": "Go",
        "enabled": False,
        "summary": "Coming later (testnet-first today).",
    },
    {
        "id": "yano",
        "name": "Yano",
        "lang": "Java",
        "enabled": False,
        "summary": "Coming later (needs GraalVM native on 4 GiB).",
    },
    {
        "id": "gerolamo",
        "name": "Gerolamo",
        "lang": "TypeScript",
        "enabled": False,
        "summary": "Coming later (measure on RockPi first).",
    },
]

_lock = threading.RLock()
_job: Dict[str, Any] = {
    "running": False,
    "op": None,
    "error": None,
    "result": None,
}


def _helper_path() -> Path:
    if HELPER.is_file():
        return HELPER
    # Dev checkout fallback
    here = Path(__file__).resolve().parents[2] / "scripts" / "cot-r-amaru-helper"
    return here if here.is_file() else HELPER


def run_helper(*args: str, timeout: float = 600.0) -> Dict[str, Any]:
    sudo = shutil.which("sudo") or "/usr/bin/sudo"
    helper = str(_helper_path())
    cmd = [sudo, "-n", helper, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        # Prefer JSON error from stdout/stderr
        for blob in (out, err):
            if blob.startswith("{"):
                try:
                    parsed = json.loads(blob)
                    if isinstance(parsed, dict):
                        parsed.setdefault("ok", False)
                        return parsed
                except json.JSONDecodeError:
                    pass
        return {
            "ok": False,
            "error": err or out or f"helper exit {proc.returncode}",
            "exit_code": proc.returncode,
        }
    if not out:
        return {"ok": True}
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            return data
        return {"ok": True, "data": data}
    except json.JSONDecodeError:
        return {"ok": True, "raw": out}


def status() -> Dict[str, Any]:
    return run_helper("status", timeout=30.0)


def _run_job(op: str, fn) -> Dict[str, Any]:
    with _lock:
        if _job.get("running"):
            return {"ok": False, "error": "another Amaru job is running", "job": job_snapshot()}
        _job.update({"running": True, "op": op, "error": None, "result": None})

    def _worker() -> None:
        try:
            result = fn()
            with _lock:
                _job["result"] = result
                if not result.get("ok"):
                    _job["error"] = result.get("error")
        except Exception as e:  # noqa: BLE001
            log.exception("amaru job %s failed", op)
            with _lock:
                _job["error"] = str(e)
                _job["result"] = {"ok": False, "error": str(e)}
        finally:
            with _lock:
                _job["running"] = False

    threading.Thread(target=_worker, name=f"cot-r-amaru-{op}", daemon=True).start()
    return {"ok": True, "started": True, "job": job_snapshot()}


def install(version: Optional[str] = None, *, background: bool = True) -> Dict[str, Any]:
    def _do() -> Dict[str, Any]:
        args = ["install"]
        if version:
            args.append(version)
        return run_helper(*args, timeout=1800.0)

    if background:
        return _run_job("install", _do)
    return _do()


def install_file(path: Path, *, background: bool = True) -> Dict[str, Any]:
    def _do() -> Dict[str, Any]:
        return run_helper("install-file", str(path), timeout=600.0)

    if background:
        return _run_job("install", _do)
    return _do()


def bootstrap(*, background: bool = True) -> Dict[str, Any]:
    def _do() -> Dict[str, Any]:
        return run_helper("bootstrap", timeout=7200.0)

    if background:
        return _run_job("bootstrap", _do)
    return _do()


def push_snapshot_job(peer_token: Optional[str], addrs: List[str]) -> Dict[str, Any]:
    def _do() -> Dict[str, Any]:
        return push_snapshot_to_peers(peer_token, addrs)

    return _run_job("push-snapshot", _do)


def wipe() -> Dict[str, Any]:
    return run_helper("wipe", timeout=120.0)


def env_get() -> Dict[str, Any]:
    return run_helper("env-get", timeout=15.0)


def env_put(vars_list: List[Dict[str, str]]) -> Dict[str, Any]:
    """Rewrite /etc/default/amaru from a list of {key,value}."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    staging = RUN_DIR / "amaru-default.env.new"
    lines: List[str] = [
        "# Managed by Cardano on the Rocks setup wizard",
        "# See https://amaru.global/user-guide/mainnet/",
    ]
    for item in vars_list:
        key = str(item.get("key") or "").strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if not key.startswith("AMARU_") and not key.startswith("OTEL_"):
            # Allow known Amaru + OTEL prefixes only
            continue
        val = str(item.get("value") if item.get("value") is not None else "")
        if any(ch in val for ch in ("\n", "\r")):
            continue
        lines.append(f"{key}={val}")
    staging.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        staging.chmod(0o640)
    except OSError:
        pass
    return run_helper("env-write", str(staging), timeout=15.0)


def service(action: str) -> Dict[str, Any]:
    action = (action or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "error": "action must be start|stop|restart"}
    return run_helper(f"service-{action}", timeout=60.0)


def set_autostart(enabled: bool) -> Dict[str, Any]:
    return run_helper(
        "enable-autostart" if enabled else "disable-autostart",
        timeout=60.0,
    )


def snapshot_export(dest: Optional[Path] = None) -> Dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = dest or (CACHE_DIR / "snapshot-mainnet.tgz")
    return run_helper("snapshot-export", str(path), timeout=1800.0)


def snapshot_import(src: Path) -> Dict[str, Any]:
    return run_helper("snapshot-import", str(src), timeout=1800.0)


def deb_path(version: Optional[str] = None) -> Dict[str, Any]:
    args = ["deb-path"]
    if version:
        args.append(version)
    return run_helper(*args, timeout=600.0)


def job_snapshot() -> Dict[str, Any]:
    with _lock:
        return {
            "running": bool(_job.get("running")),
            "op": _job.get("op"),
            "error": _job.get("error"),
            "result": _job.get("result"),
        }


def catalog() -> List[Dict[str, Any]]:
    return list(CATALOG)


def cluster_status(peer_token: Optional[str], adopted: List[Dict[str, Any]]) -> Dict[str, Any]:
    local = status()
    peers_out: List[Dict[str, Any]] = []
    for entry in adopted:
        addr = str(entry.get("addr") or "")
        if not addr or not peer_token:
            continue
        resp = peers.http_json(
            "GET",
            peers.peer_url(addr, "/api/v1/peer/node/status"),
            token=peer_token,
            timeout=5.0,
        )
        peers_out.append(
            {
                "addr": addr,
                "device_label": entry.get("device_label") or entry.get("label"),
                "status": resp,
            }
        )
    return {
        "ok": True,
        "catalog": catalog(),
        "local": local,
        "job": job_snapshot(),
        "adopted": peers_out,
    }


def push_snapshot_to_peers(
    peer_token: Optional[str],
    addrs: List[str],
) -> Dict[str, Any]:
    """Export local snapshot and POST multipart-ish via HTTP JSON path: peer pulls from us."""
    if not peer_token:
        return {"ok": False, "error": "peer token missing"}
    # Ensure export exists
    export = snapshot_export()
    if not export.get("ok"):
        return export
    path = Path(str(export.get("path") or ""))
    if not path.is_file():
        return {"ok": False, "error": "export missing"}
    local_ip = peers.local_mgmt_ipv4()
    results = []
    for addr in addrs:
        # Peer installs package if needed, then pulls bundle from master
        install_resp = peers.http_json(
            "POST",
            peers.peer_url(addr, "/api/v1/peer/node/install"),
            body={"from_master": local_ip},
            token=peer_token,
            timeout=1800.0,
        )
        pull = peers.http_json(
            "POST",
            peers.peer_url(addr, "/api/v1/peer/node/snapshot/pull"),
            body={
                "master": local_ip,
                "url_path": "/api/v1/peer/node/snapshot/bundle",
            },
            token=peer_token,
            timeout=3600.0,
        )
        results.append(
            {
                "addr": addr,
                "install": install_resp,
                "pull": pull,
            }
        )
    def _peer_ok(r: Dict[str, Any]) -> bool:
        inst = r.get("install") or {}
        pull = r.get("pull") or {}
        install_ok = bool(inst.get("ok") or inst.get("skipped") or inst.get("installed"))
        return install_ok and bool(pull.get("ok"))

    ok = all(_peer_ok(r) for r in results) if results else False
    return {"ok": ok, "export": export, "peers": results}
