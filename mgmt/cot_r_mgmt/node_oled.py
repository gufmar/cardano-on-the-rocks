# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""OLED carousel for wizard Nodes (3.x): logo, progress, CPU, memory, tip, mempool."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cot_r_mgmt import amaru as amarumod
from cot_r_mgmt import display_client
from cot_r_mgmt.wizard import STEP_CONFIGURING, WizardStore

log = logging.getLogger("cot_r_mgmt.node_oled")

SLIDE_SECONDS = float(os.environ.get("COT_R_NODE_OLED_SLIDE", "3.5"))
SAMPLE_SECONDS = float(os.environ.get("COT_R_NODE_OLED_SAMPLE", "2.0"))
AMARU_UNIT = os.environ.get("COT_R_AMARU_SERVICE", "amaru.service")
# Gaps at or below this are still "on tip" (jitter / short peer lag).
ON_TIP_GAP = int(os.environ.get("COT_R_NODE_OLED_ON_TIP_GAP", "300"))


def _read_load() -> Tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        with open("/proc/loadavg", encoding="utf-8") as fh:
            a, b, c = fh.read().split()[:3]
        return float(a), float(b), float(c)
    except (OSError, ValueError):
        return None, None, None


def _read_cpu_times() -> List[Tuple[int, int]]:
    """Per-CPU (idle_all, total) jiffies from /proc/stat."""
    out: List[Tuple[int, int]] = []
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("cpu") or line.startswith("cpu "):
                    continue
                if not line[3].isdigit():
                    continue
                parts = line.split()
                # user nice system idle iowait irq softirq steal …
                vals = [int(x) for x in parts[1:8]]
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                total = sum(vals)
                out.append((idle, total))
    except (OSError, ValueError):
        return []
    return out


def _read_temps_c() -> Dict[str, float]:
    out: Dict[str, float] = {}
    base = Path("/sys/class/thermal")
    if not base.is_dir():
        return out
    for zone in sorted(base.glob("thermal_zone*")):
        try:
            name = (zone / "type").read_text(encoding="utf-8").strip()
            milli = int((zone / "temp").read_text(encoding="utf-8").strip())
            out[name] = milli / 1000.0
        except (OSError, ValueError):
            continue
    return out


def _meminfo_mb() -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return total, available, used MiB."""
    try:
        info: Dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                info[k.strip()] = int(v.strip().split()[0])
        total = info.get("MemTotal", 0) / 1024.0
        avail = info.get("MemAvailable", 0) / 1024.0
        used = max(0.0, total - avail)
        return round(total, 1), round(avail, 1), round(used, 1)
    except (OSError, ValueError):
        return None, None, None


def _amaru_pid() -> Optional[int]:
    import subprocess

    try:
        out = subprocess.check_output(
            ["systemctl", "show", "-p", "MainPID", "--value", AMARU_UNIT],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
        ).strip()
        pid = int(out or "0")
        return pid if pid > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _proc_rss_hwm_mb(pid: int) -> Tuple[Optional[float], Optional[float]]:
    try:
        rss = hwm = None
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) / 1024.0
                elif line.startswith("VmHWM:"):
                    hwm = int(line.split()[1]) / 1024.0
        return (
            round(rss, 1) if rss is not None else None,
            round(hwm, 1) if hwm is not None else None,
        )
    except (OSError, ValueError):
        return None, None


def _point_block_slot(raw: Any) -> Tuple[Optional[int], Optional[int]]:
    """Parse Amaru [slot, hash, block] tuples."""
    if not isinstance(raw, list) or not raw:
        return None, None
    slot = block = None
    try:
        slot = int(raw[0])
    except (TypeError, ValueError):
        slot = None
    if len(raw) > 2:
        try:
            block = int(raw[2])
        except (TypeError, ValueError):
            block = None
    return slot, block


def _journal_tip() -> Dict[str, Any]:
    """Parse recent Amaru tip events (filtered) for OLED /status."""
    import json
    import subprocess

    tip: Dict[str, Any] = {
        "slot": None,
        "block_no": None,
        "gap_blocks": None,
        "network_block": None,
        "tx_count": None,
        "phase": None,
    }
    raw = ""
    for cmd in (
        [
            "journalctl",
            "-u",
            AMARU_UNIT,
            "-n",
            "1500",
            "-o",
            "cat",
            "--no-pager",
            "--grep",
            r"tip\.(adopt|update)|chainsync\.(roll_forward_done|intersect_found|roll_backward)|chain\.(tip_accepted|best_tip_candidate)",
        ],
        [
            "journalctl",
            "-u",
            AMARU_UNIT,
            "-n",
            "400",
            "-p",
            "info..alert",
            "-o",
            "cat",
            "--no-pager",
        ],
    ):
        try:
            raw = subprocess.check_output(
                cmd,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=4.0,
            )
        except (OSError, subprocess.SubprocessError):
            raw = ""
        if raw.strip():
            break
    if not raw:
        return tip

    local_block = local_slot = None
    net_block = None
    tx_count = None
    for line in raw.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        fields = obj.get("fields") or {}
        msg = fields.get("message")
        if isinstance(msg, dict):
            msg = msg.get("name")
        msg = str(msg or "")
        if msg in ("tip.adopt", "tip.update"):
            try:
                if fields.get("slot") is not None:
                    local_slot = int(fields["slot"])
                if fields.get("block_height") is not None:
                    local_block = int(fields["block_height"])
            except (TypeError, ValueError):
                pass
            if msg == "tip.update" and fields.get("tx_count") is not None:
                try:
                    tx_count = int(fields["tx_count"])
                except (TypeError, ValueError):
                    pass
        elif msg in (
            "chainsync.roll_forward_done",
            "chainsync.intersect_found",
            "chainsync.roll_backward",
        ):
            s_slot, s_block = _point_block_slot(fields.get("current"))
            if s_slot is not None:
                local_slot = s_slot
            if s_block is not None:
                local_block = s_block
            _n_slot, n_block = _point_block_slot(
                fields.get("highest") or fields.get("tip")
            )
            if n_block is not None:
                net_block = n_block
            if fields.get("tip_height") is not None:
                try:
                    net_block = int(fields["tip_height"])
                except (TypeError, ValueError):
                    pass
        elif msg in ("chain.tip_accepted", "chain.best_tip_candidate"):
            s_slot, s_block = _point_block_slot(fields.get("tip"))
            if s_slot is not None:
                local_slot = s_slot
            if s_block is not None:
                local_block = s_block

    tip["slot"] = local_slot
    tip["block_no"] = local_block
    tip["network_block"] = net_block
    tip["tx_count"] = tx_count
    if local_block is not None and net_block is not None:
        tip["gap_blocks"] = max(0, net_block - local_block)
        tip["phase"] = (
            "on_tip" if tip["gap_blocks"] <= ON_TIP_GAP else "catching_up"
        )
    elif local_block is not None or local_slot is not None:
        tip["phase"] = "on_tip"
    return tip


class NodeOled:
    """Rotate OLED slides while the wizard is in Nodes (3.x)."""

    def __init__(
        self,
        *,
        wizard: WizardStore,
        should_run: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.wizard = wizard
        self._should_run = should_run or self._default_should_run
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._slide_idx = 0
        self._last_tip_block: Optional[int] = None
        self._os_used_peak: Optional[float] = None
        self._node_rss_peak: Optional[float] = None
        self._status_cache: Dict[str, Any] = {}
        self._status_at = 0.0
        self._prev_cpu: Optional[List[Tuple[int, int]]] = None
        self.active = False

    def _default_should_run(self) -> bool:
        wiz = self.wizard.snapshot()
        return bool(wiz.get("confirmed") or wiz.get("ui_step", 0) >= STEP_CONFIGURING)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cot-r-node-oled", daemon=True)
        self._thread.start()
        log.info("node OLED carousel started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self.active = False

    def reset_peaks(self) -> None:
        """Clear peak memory / tip latch (demo reset)."""
        self._os_used_peak = None
        self._node_rss_peak = None
        self._last_tip_block = None
        self._slide_idx = 0
        self._status_cache = {}
        self._status_at = 0.0
        self._prev_cpu = None

    def _refresh_status(self) -> Dict[str, Any]:
        now = time.monotonic()
        if now - self._status_at < 5.0 and self._status_cache:
            return self._status_cache
        try:
            st = amarumod.status()
        except Exception:  # noqa: BLE001
            st = {}
        job = {}
        try:
            job = amarumod.job_snapshot()
        except Exception:  # noqa: BLE001
            pass
        self._status_cache = {"local": st, "job": job}
        self._status_at = now
        return self._status_cache

    def _core_pct(self) -> List[float]:
        cur = _read_cpu_times()
        if not cur:
            return []
        prev = self._prev_cpu
        self._prev_cpu = cur
        if not prev or len(prev) != len(cur):
            return [0.0] * len(cur)
        out: List[float] = []
        for (idle0, tot0), (idle1, tot1) in zip(prev, cur):
            dt = tot1 - tot0
            di = idle1 - idle0
            if dt <= 0:
                out.append(0.0)
            else:
                busy = max(0.0, min(1.0, 1.0 - (di / float(dt))))
                out.append(round(100.0 * busy, 1))
        return out

    def _sample(self) -> Dict[str, Any]:
        load1, load5, load15 = _read_load()
        temps = _read_temps_c()
        total, avail, used = _meminfo_mb()
        if used is not None:
            self._os_used_peak = (
                used if self._os_used_peak is None else max(self._os_used_peak, used)
            )
        pid = _amaru_pid()
        rss = hwm = None
        if pid is not None:
            rss, hwm = _proc_rss_hwm_mb(pid)
            if rss is not None:
                self._node_rss_peak = (
                    rss if self._node_rss_peak is None else max(self._node_rss_peak, rss)
                )
            if hwm is not None:
                self._node_rss_peak = (
                    hwm
                    if self._node_rss_peak is None
                    else max(self._node_rss_peak, hwm)
                )
        tip = _journal_tip()
        bundle = self._refresh_status()
        local = bundle.get("local") or {}
        job = bundle.get("job") or {}
        run = local.get("run") or {}
        # Prefer helper projection when present
        if run.get("block_no") is not None:
            tip["block_no"] = tip["block_no"] or run.get("block_no")
            tip["slot"] = tip["slot"] or run.get("slot")
            tip["gap_blocks"] = run.get("gap_blocks", tip.get("gap_blocks"))
            net = run.get("network_tip") or {}
            if net.get("block_no") is not None:
                tip["network_block"] = net.get("block_no")
        # Recompute on-tip with OLED tolerance. Do not inherit helper "starting"
        # once the service is active (common after reboot while journals settle).
        gap = tip.get("gap_blocks")
        if gap is not None:
            tip["phase"] = "on_tip" if int(gap) <= ON_TIP_GAP else "catching_up"
        elif tip.get("phase") in ("on_tip", "catching_up"):
            pass
        elif run.get("phase") in ("on_tip", "near_tip"):
            tip["phase"] = "on_tip"
        elif run.get("phase") == "catching_up":
            tip["phase"] = "catching_up"
        elif local.get("service_active") == "active":
            tip["phase"] = tip.get("phase") or "running"
        return {
            "load": (load1, load5, load15),
            "cores": self._core_pct(),
            "temps": temps,
            "mem_total": total,
            "mem_avail": avail,
            "mem_used": used,
            "mem_used_peak": self._os_used_peak,
            "node_rss": rss,
            "node_rss_peak": self._node_rss_peak,
            "node_running": bool(pid),
            "tip": tip,
            "local": local,
            "job": job,
        }

    def _phase_label(self, sample: Dict[str, Any]) -> Tuple[str, str, str]:
        local = sample.get("local") or {}
        job = sample.get("job") or {}
        tip = sample.get("tip") or {}
        if job.get("running"):
            op = str(job.get("op") or "job")
            detail = str(job.get("detail") or "")[:18]
            return op[:18], detail or "working…", "Amaru"
        if local.get("bootstrap_active") in ("activating", "active"):
            prog = local.get("progress") or {}
            mib = prog.get("downloaded_mib") or prog.get("delta_mib")
            line = f"{mib} MiB" if mib is not None else "Mithril…"
            return "Bootstrap", str(line), "Amaru"
        if local.get("service_active") == "active":
            gap = tip.get("gap_blocks")
            phase = tip.get("phase")
            # Never show Catch-up without a measured gap (post-reboot DEBUG flood
            # used to leave tip empty and falsely said "Catch-up / syncing").
            if phase == "on_tip" or (gap is not None and int(gap) <= ON_TIP_GAP):
                if gap is None or int(gap) == 0:
                    sub = "synced"
                else:
                    sub = f"ok Δ{int(gap)}"
                return "On tip", sub, "Amaru"
            if gap is not None and int(gap) > ON_TIP_GAP:
                return "Catch-up", f"−{int(gap)} blk", "Amaru"
            if tip.get("block_no") is not None:
                return "Running", f"block {tip.get('block_no')}", "Amaru"
            return "Running", "tip pending…", "Amaru"
        if local.get("installed"):
            if local.get("db_present") or local.get("bootstrap_done"):
                return "Ready", "Start Amaru", "Amaru"
            return "Ready", "Bootstrap DB", "Amaru"
        return "Install", "Amaru .deb", "Amaru"

    def _qr_slides(self, sample: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Wi-Fi join + status dashboard QR when the node is up."""
        from cot_r_mgmt import onboarding as onboard
        from cot_r_mgmt import peers

        local = sample.get("local") or {}
        if local.get("service_active") != "active":
            return []
        slides: List[Dict[str, Any]] = []
        digit = onboard.board_digit()
        if onboard.ap_is_enabled():
            ssid = onboard.read_ssid()
            psk = onboard.read_psk()
            if ssid and psk:
                slides.append(
                    {
                        "kind": "qr",
                        "title": "Wi-Fi",
                        "digit": digit,
                        "lines": [ssid[:9], "join AP"],
                        "qr_data": onboard.wifi_qr_payload(ssid, psk),
                        "qr_side": "right",
                    }
                )
        ip = peers.local_mgmt_ipv4()
        if ip:
            url = onboard.status_url(ip)
            slides.append(
                {
                    "kind": "qr",
                    "title": "Status",
                    "digit": digit,
                    "lines": ["Amaru", "status"],
                    "qr_data": url,
                    "qr_side": "left",
                }
            )
        return slides

    def _slides(self, sample: Dict[str, Any]) -> List[Dict[str, Any]]:
        title, line2, line3 = self._phase_label(sample)
        tip = sample.get("tip") or {}
        load = sample.get("load") or (None, None, None)
        temps = sample.get("temps") or {}
        cpu_t = temps.get("cpu-thermal")
        gpu_t = temps.get("gpu-thermal")
        cores = list(sample.get("cores") or [])
        slides: List[Dict[str, Any]] = [
            {"kind": "logo"},
            {
                "kind": "progress",
                "title": title,
                "line2": line2,
                "line3": line3,
            },
        ]
        slides.extend(self._qr_slides(sample))
        slides.extend(
            [
                {
                    "kind": "cpu",
                    "load1": load[0],
                    "load5": load[1],
                    "load15": load[2],
                    "cpu_c": cpu_t,
                    "gpu_c": gpu_t,
                    "nproc": os.cpu_count(),
                    "cores": cores,
                },
                {
                    "kind": "memory",
                    "os_used": sample.get("mem_used"),
                    "os_peak": sample.get("mem_used_peak"),
                    "os_total": sample.get("mem_total"),
                    "node_rss": sample.get("node_rss"),
                    "node_peak": sample.get("node_rss_peak"),
                    "node_running": sample.get("node_running"),
                },
                {
                    "kind": "tip",
                    "block_no": tip.get("block_no"),
                    "slot": tip.get("slot"),
                    "gap_blocks": tip.get("gap_blocks"),
                    "phase": tip.get("phase"),
                    "prev_block_no": self._last_tip_block,
                    "animate": bool(
                        tip.get("block_no") is not None
                        and self._last_tip_block is not None
                        and tip.get("block_no") != self._last_tip_block
                    ),
                },
                {
                    "kind": "mempool",
                    "mempool_tx": None,  # not in Amaru JSON traces yet
                    "last_block_tx": tip.get("tx_count"),
                    "block_no": tip.get("block_no"),
                },
            ]
        )
        return slides

    def watch_snapshot(self) -> Dict[str, Any]:
        """Public read-only fields for the /status dashboard."""
        sample = self._sample()
        tip = sample.get("tip") or {}
        local = sample.get("local") or {}
        load = sample.get("load") or (None, None, None)
        run = local.get("run") or {}
        title, line2, line3 = self._phase_label(sample)
        return {
            "ok": True,
            "phase": tip.get("phase") or local.get("service_active"),
            "headline": title,
            "subline": line2,
            "brand": line3,
            "service_active": local.get("service_active"),
            "installed": local.get("installed"),
            "version": local.get("version"),
            "tip": {
                "slot": tip.get("slot"),
                "block_no": tip.get("block_no"),
                "gap_blocks": tip.get("gap_blocks"),
                "network_block": tip.get("network_block"),
                "tx_count": tip.get("tx_count"),
                "phase": tip.get("phase"),
            },
            "network_tip": (run.get("network_tip") or {}),
            "sync_pct": run.get("sync_pct"),
            "load": {"1": load[0], "5": load[1], "15": load[2]},
            "cores": sample.get("cores") or [],
            "temps": sample.get("temps") or {},
            "memory": {
                "total_mib": sample.get("mem_total"),
                "used_mib": sample.get("mem_used"),
                "avail_mib": sample.get("mem_avail"),
                "node_rss_mib": sample.get("node_rss"),
                "node_running": sample.get("node_running"),
            },
        }

    def _loop(self) -> None:
        while not self._stop.wait(0.2):
            if not self._should_run():
                self.active = False
                if self._stop.wait(SAMPLE_SECONDS):
                    break
                continue
            self.active = True
            wait = SLIDE_SECONDS
            try:
                sample = self._sample()
                slides = self._slides(sample)
                if not slides:
                    continue
                idx = self._slide_idx % len(slides)
                slide = slides[idx]
                self._slide_idx = (idx + 1) % len(slides)
                ttl = int(SLIDE_SECONDS * 1000)
                if slide.get("kind") == "tip" and slide.get("animate"):
                    ttl = max(ttl, 4200)
                if slide.get("kind") == "qr":
                    ttl = max(ttl, 7000)
                    wait = 7.0
                display_client.show_node_slide(slide, ttl_ms=ttl, priority=72)
                block = (sample.get("tip") or {}).get("block_no")
                if block is not None:
                    self._last_tip_block = int(block)
            except Exception:  # noqa: BLE001
                log.exception("node OLED tick failed")
            if self._stop.wait(wait):
                break
