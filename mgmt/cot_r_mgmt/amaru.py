# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Amaru node control via privileged helper (install / bootstrap / env / service)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from cot_r_mgmt import peers

log = logging.getLogger("cot_r_mgmt.amaru")

HELPER = Path(os.environ.get("COT_R_AMARU_HELPER", "/opt/cot-r/bin/cot-r-amaru-helper"))
RUN_DIR = Path(os.environ.get("COT_R_RUN_DIR", "/run/cot-r"))
CACHE_DIR = Path(os.environ.get("COT_R_AMARU_CACHE", "/var/cache/cot-r/amaru"))
BOOTSTRAP_POLL_SECONDS = float(os.environ.get("COT_R_AMARU_BOOTSTRAP_POLL", "3.0"))
BOOTSTRAP_MAX_SECONDS = float(os.environ.get("COT_R_AMARU_BOOTSTRAP_MAX", str(6 * 3600)))

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
_cancel = threading.Event()
_job: Dict[str, Any] = {
    "running": False,
    "op": None,
    "error": None,
    "result": None,
    "progress": None,
    "detail": None,
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


def _set_job_progress(progress: Optional[Dict[str, Any]], detail: Optional[str] = None) -> None:
    with _lock:
        if progress is not None:
            _job["progress"] = progress
        if detail is not None:
            _job["detail"] = detail


def _run_job(op: str, fn) -> Dict[str, Any]:
    with _lock:
        if _job.get("running"):
            return {"ok": False, "error": "another Amaru job is running", "job": job_snapshot()}
        _cancel.clear()
        _job.update(
            {
                "running": True,
                "op": op,
                "error": None,
                "result": None,
                "progress": None,
                "detail": f"starting {op}",
            }
        )

    def _worker() -> None:
        try:
            result = fn()
            with _lock:
                _job["result"] = result
                if not result.get("ok"):
                    _job["error"] = result.get("error") or result.get("detail") or "failed"
                    _job["detail"] = _job["error"]
                else:
                    _job["detail"] = result.get("detail") or "done"
                    if result.get("progress") is not None:
                        _job["progress"] = result.get("progress")
        except Exception as e:  # noqa: BLE001
            log.exception("amaru job %s failed", op)
            with _lock:
                _job["error"] = str(e)
                _job["detail"] = str(e)
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


def install_on_peer(peer_token: Optional[str], addr: str) -> Dict[str, Any]:
    """Install Amaru on an adopted peer from this master's cached .deb (LAN)."""
    if not peer_token:
        return {"ok": False, "error": "peer token missing"}
    local_ip = peers.local_mgmt_ipv4()
    if not local_ip:
        return {"ok": False, "error": "local management IP unknown"}

    def _do() -> Dict[str, Any]:
        # Ensure a .deb exists in cache for the peer to pull.
        deb = deb_path()
        if not deb.get("ok"):
            return deb
        resp = peers.http_json(
            "POST",
            peers.peer_url(addr, "/api/v1/peer/node/install"),
            body={"from_master": local_ip},
            token=peer_token,
            timeout=1800.0,
        )
        if resp.get("unreachable"):
            return {"ok": False, "error": f"peer {addr} unreachable", "peer": resp}
        if resp.get("skipped"):
            return {"ok": True, "skipped": True, "addr": addr, "peer": resp}
        ok = bool(resp.get("ok") or resp.get("installed"))
        return {"ok": ok, "addr": addr, "peer": resp, "error": None if ok else resp.get("error")}

    return _run_job("install-peer", _do)


def install_file(path: Path, *, background: bool = True) -> Dict[str, Any]:
    def _do() -> Dict[str, Any]:
        return run_helper("install-file", str(path), timeout=600.0)

    if background:
        return _run_job("install", _do)
    return _do()


def _bootstrap_monitor() -> Dict[str, Any]:
    """Start oneshot bootstrap (non-blocking) and poll until done or failed."""
    started = run_helper("bootstrap", timeout=60.0)
    if not started.get("ok"):
        err = started.get("error") or "failed to start bootstrap"
        prog = started.get("progress")
        return {"ok": False, "error": err, "progress": prog, "detail": err}

    deadline = time.time() + BOOTSTRAP_MAX_SECONDS
    last_prog: Optional[Dict[str, Any]] = started.get("progress")
    _set_job_progress(last_prog, "bootstrap started")

    while time.time() < deadline:
        if _cancel.is_set():
            return {
                "ok": False,
                "error": "bootstrap cancelled",
                "progress": last_prog,
                "detail": "bootstrap cancelled",
                "cancelled": True,
            }
        st = status()
        if not st.get("ok"):
            time.sleep(BOOTSTRAP_POLL_SECONDS)
            continue
        prog = st.get("progress") if isinstance(st.get("progress"), dict) else {}
        last_prog = prog
        unit = str(st.get("bootstrap_active") or prog.get("unit_state") or "")
        detail_parts = []
        dl = prog.get("downloaded_bytes")
        delta = prog.get("delta_bytes")
        if dl is not None:
            detail_parts.append(f"downloaded {_fmt_mib(dl)}")
        if delta is not None:
            detail_parts.append(f"disk +{_fmt_mib(delta)}")
        snaps = prog.get("completed_snapshots")
        if snaps is not None:
            detail_parts.append(f"snapshots {snaps}")
        detail_parts.append(f"unit {unit or '?'}")
        detail = " · ".join(detail_parts)
        _set_job_progress(prog, detail)

        if st.get("bootstrap_done") or st.get("db_present") or prog.get("complete"):
            return {
                "ok": True,
                "detail": "bootstrap complete",
                "progress": prog,
                **{k: st.get(k) for k in ("installed", "version", "bootstrap_done", "db_present")},
            }

        if prog.get("failed") or unit == "failed":
            err = (
                prog.get("error")
                or "amaru-bootstrap.service failed"
            )
            # Never use systemd accounting footers as the error reason.
            last = str(prog.get("last_log") or "")
            if err and "Consumed" in str(err):
                err = "amaru-bootstrap.service failed"
            if last and "Consumed" not in last and "Deactivated successfully" not in last:
                err = f"{err}: {last}" if err else last
            return {"ok": False, "error": err, "progress": prog, "detail": err}

        # Unit finished: success if Amaru logged complete / DBs present / systemd success
        if unit in ("inactive", "dead"):
            time.sleep(2.0)
            if _cancel.is_set():
                return {
                    "ok": False,
                    "error": "bootstrap cancelled",
                    "progress": last_prog,
                    "detail": "bootstrap cancelled",
                    "cancelled": True,
                }
            st2 = status()
            prog2 = st2.get("progress") if isinstance(st2.get("progress"), dict) else prog
            if (
                st2.get("db_present")
                or st2.get("bootstrap_done")
                or (prog2 or {}).get("complete")
                or (prog2 or {}).get("systemd_result") == "success"
            ):
                return {"ok": True, "detail": "bootstrap complete", "progress": prog2}
            unit2 = str(st2.get("bootstrap_active") or "")
            if unit2 in ("active", "activating"):
                time.sleep(BOOTSTRAP_POLL_SECONDS)
                continue
            err = (prog2 or {}).get("error") or "bootstrap stopped without creating databases"
            if isinstance(err, str) and "Consumed" in err:
                err = "bootstrap stopped without creating databases"
            return {"ok": False, "error": err, "progress": prog2, "detail": err}

        time.sleep(BOOTSTRAP_POLL_SECONDS)

    err = "bootstrap timed out"
    return {"ok": False, "error": err, "progress": last_prog, "detail": err}


def _fmt_mib(num: Any) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "?"
    return f"{n / (1024 * 1024):.1f} MiB"


def bootstrap(*, background: bool = True) -> Dict[str, Any]:
    if background:
        return _run_job("bootstrap", _bootstrap_monitor)
    return _bootstrap_monitor()


def bootstrap_stop() -> Dict[str, Any]:
    """Stop amaru-bootstrap / Mithril download; cancel in-process job monitor."""
    _cancel.set()
    with _lock:
        if _job.get("running") and _job.get("op") == "bootstrap":
            _job["detail"] = "stopping bootstrap…"
    result = run_helper("bootstrap-stop", timeout=60.0)
    with _lock:
        if _job.get("running") and _job.get("op") == "bootstrap":
            _job["error"] = "bootstrap cancelled"
            _job["detail"] = "bootstrap cancelled"
            _job["result"] = {"ok": False, "error": "bootstrap cancelled", "cancelled": True}
            _job["running"] = False
    result.setdefault("job", job_snapshot())
    return result


def push_snapshot_job(peer_token: Optional[str], addrs: List[str]) -> Dict[str, Any]:
    def _do() -> Dict[str, Any]:
        return push_snapshot_to_peers(peer_token, addrs)

    return _run_job("push-snapshot", _do)


def peer_service(peer_token: Optional[str], addr: str, action: str) -> Dict[str, Any]:
    if not peer_token:
        return {"ok": False, "error": "peer token missing"}
    action = (action or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "error": "action must be start|stop|restart"}
    resp = peers.http_json(
        "POST",
        peers.peer_url(addr, "/api/v1/peer/node/service"),
        body={"action": action},
        token=peer_token,
        timeout=60.0,
    )
    if resp.get("unreachable"):
        return {"ok": False, "error": f"peer {addr} unreachable", "peer": resp}
    return {"ok": bool(resp.get("ok")), "addr": addr, "peer": resp, "error": resp.get("error")}


def peer_wipe(peer_token: Optional[str], addr: str) -> Dict[str, Any]:
    if not peer_token:
        return {"ok": False, "error": "peer token missing"}
    resp = peers.http_json(
        "POST",
        peers.peer_url(addr, "/api/v1/peer/node/wipe"),
        body={},
        token=peer_token,
        timeout=120.0,
    )
    if resp.get("unreachable"):
        return {"ok": False, "error": f"peer {addr} unreachable", "peer": resp}
    return {"ok": bool(resp.get("ok")), "addr": addr, "peer": resp, "error": resp.get("error")}


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


def tune_catchup(restart: bool = True) -> Dict[str, Any]:
    """Quiet DEBUG journal spam + raise MemoryHigh for RockPi catch-up."""
    return run_helper(
        "tune-catchup",
        "1" if restart else "0",
        timeout=90.0,
    )


def set_autostart(enabled: bool) -> Dict[str, Any]:
    return run_helper(
        "enable-autostart" if enabled else "disable-autostart",
        timeout=60.0,
    )


def snapshot_cache_clear() -> Dict[str, Any]:
    """Drop on-disk snapshot tarballs under the Amaru cache (frees ~10 GiB)."""
    return run_helper("snapshot-cache-clear", timeout=120.0)


def snapshot_size() -> Dict[str, Any]:
    """Bytes of chain/ledger/snapshots under AMARU_HOME (for disk checks / UI)."""
    return run_helper("snapshot-size", timeout=60.0)


def snapshot_export(
    dest: Optional[Path] = None,
    *,
    reuse: bool = True,
    min_reuse_bytes: int = 100 * 1024 * 1024,
) -> Dict[str, Any]:
    """Legacy gzip-to-disk export. Prefer stream helpers for LAN push."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = dest or (CACHE_DIR / "snapshot-mainnet.tgz")
    if reuse and path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size >= min_reuse_bytes:
            return {
                "ok": True,
                "path": str(path),
                "bytes": size,
                "reused": True,
                "detail": f"reusing existing snapshot ({size} bytes)",
            }
    return run_helper("snapshot-export", str(path), timeout=1800.0)


def snapshot_import(src: Path) -> Dict[str, Any]:
    return run_helper("snapshot-import", str(src), timeout=1800.0)


def _helper_cmd(*args: str) -> List[str]:
    sudo = shutil.which("sudo") or "/usr/bin/sudo"
    return [sudo, "-n", str(_helper_path()), *args]


def open_snapshot_stream_out() -> subprocess.Popen:
    """Stop Amaru and stream uncompressed tar of DB dirs on stdout."""
    return subprocess.Popen(
        _helper_cmd("snapshot-stream-out"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


def run_snapshot_stream_in(src) -> Dict[str, Any]:
    """Pipe a readable binary stream into snapshot-stream-in (extract to AMARU_HOME)."""
    proc = subprocess.Popen(
        _helper_cmd("snapshot-stream-in"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            proc.stdin.write(chunk)
        proc.stdin.close()
    except Exception as e:  # noqa: BLE001
        try:
            proc.kill()
        except OSError:
            pass
        return {"ok": False, "error": f"stream write failed: {e}"}
    out_b, err_b = proc.communicate(timeout=7200)
    out = (out_b or b"").decode("utf-8", errors="replace").strip()
    err = (err_b or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
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
            "error": err or out or f"snapshot-stream-in exit {proc.returncode}",
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
            "detail": _job.get("detail"),
            "progress": _job.get("progress"),
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
    """Stream local Amaru DB folders to peers over LAN (no on-disk tarball)."""
    if not peer_token:
        return {"ok": False, "error": "peer token missing"}

    _set_job_progress({"phase": "cache_clear"}, "clearing old snapshot tarballs…")
    cleared = snapshot_cache_clear()
    if not cleared.get("ok"):
        log.warning("snapshot cache clear: %s", cleared.get("error") or cleared)

    _set_job_progress({"phase": "size"}, "measuring local Amaru DB size…")
    size_info = snapshot_size()
    if not size_info.get("ok"):
        return {
            "ok": False,
            "error": size_info.get("error") or "snapshot size failed",
            "size": size_info,
        }
    size = int(size_info.get("bytes") or 0)
    if size <= 0:
        return {"ok": False, "error": "local DB size is zero", "size": size_info}

    local_ip = peers.local_mgmt_ipv4()
    if not local_ip:
        return {"ok": False, "error": "local management IP unknown", "size": size_info}

    _set_job_progress(
        {"phase": "ready", "bytes": size},
        f"streaming ~{size // (1024 * 1024)} MiB DB folders to peers (Amaru stops while serving)…",
    )

    results = []
    try:
        for addr in addrs:
            _set_job_progress(
                {"phase": "peer_install", "addr": addr, "bytes": size},
                f"{addr}: ensure Amaru package…",
            )
            install_resp = peers.http_json(
                "POST",
                peers.peer_url(addr, "/api/v1/peer/node/install"),
                body={"from_master": local_ip},
                token=peer_token,
                timeout=1800.0,
            )
            _set_job_progress(
                {"phase": "peer_pull", "addr": addr, "bytes": size},
                f"{addr}: streaming ~{size // (1024 * 1024)} MiB over LAN…",
            )
            pull = peers.http_json(
                "POST",
                peers.peer_url(addr, "/api/v1/peer/node/snapshot/pull"),
                body={
                    "master": local_ip,
                    "url_path": "/api/v1/peer/node/snapshot/stream",
                    "mode": "stream",
                    "expect_bytes": size,
                },
                token=peer_token,
                timeout=7200.0,
            )
            results.append(
                {
                    "addr": addr,
                    "install": install_resp,
                    "pull": pull,
                }
            )
    finally:
        # stream-out stops Amaru; restore so this board can keep catching tip.
        try:
            service("start")
        except Exception:  # noqa: BLE001
            log.exception("failed to restart amaru after snapshot stream")

    def _peer_ok(r: Dict[str, Any]) -> bool:
        inst = r.get("install") or {}
        pull = r.get("pull") or {}
        install_ok = bool(inst.get("ok") or inst.get("skipped") or inst.get("installed"))
        return install_ok and bool(pull.get("ok"))

    ok = all(_peer_ok(r) for r in results) if results else False
    errors = []
    for r in results:
        addr = r.get("addr")
        pull = r.get("pull") or {}
        inst = r.get("install") or {}
        if not (inst.get("ok") or inst.get("skipped") or inst.get("installed")):
            errors.append(f"{addr} install: {inst.get('error') or 'failed'}")
        if not pull.get("ok"):
            errors.append(f"{addr} pull: {pull.get('error') or 'failed'}")
    err = "; ".join(errors) if errors else None
    return {
        "ok": ok,
        "size": size_info,
        "cache_clear": cleared,
        "peers": results,
        "error": err,
        "detail": "done" if ok else (err or "push-snapshot failed"),
    }
