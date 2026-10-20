# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""MikroTik mAP uplink control over SSH (legacy /interface wireless)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("cot_r_mgmt.routeros")

# 802.11 SSID max 32 octets; keep PSKs in WPA-PSK range.
SSID_MAX_LEN = 32
PSK_MAX_LEN = 63

ETC_DIR = Path(os.environ.get("COT_R_ETC_DIR", "/etc/cot-r"))
DEVICE_ENV = ETC_DIR / "device.env"
SSH_DIR = ETC_DIR / "ssh"
SSH_KEY = Path(os.environ.get("COT_R_MAP_SSH_KEY", str(SSH_DIR / "id_ed25519")))
MAP_HOST = os.environ.get("COT_R_MAP_HOST", "172.27.213.1")
WLAN_STA = os.environ.get("COT_R_MAP_WLAN", "wlan1")
SEC_PROFILE = os.environ.get("COT_R_MAP_SEC_PROFILE", "uplink")
SSH_TIMEOUT = int(os.environ.get("COT_R_MAP_SSH_TIMEOUT", "20"))
# cot-r home is /var/lib/cot-r/home; keep known_hosts there for BatchMode+accept-new.
KNOWN_HOSTS = Path(
    os.environ.get(
        "COT_R_MAP_KNOWN_HOSTS",
        "/var/lib/cot-r/home/.ssh/known_hosts",
    )
)


def _exists_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _env_file_value(path: Path, key: str) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip("'").strip('"')
    return None


def map_user() -> str:
    """RouterOS username for this board (default rockpi1/2/3, not admin)."""
    override = os.environ.get("COT_R_MAP_USER") or _env_file_value(DEVICE_ENV, "COT_R_MAP_USER")
    if override:
        return override
    device_id = (
        os.environ.get("COT_R_DEVICE_ID")
        or _env_file_value(DEVICE_ENV, "COT_R_DEVICE_ID")
        or ""
    ).strip().lower()
    # rockpi-2 / rockpi2 / 2 -> rockpi2
    if device_id in ("1", "2", "3"):
        return f"rockpi{device_id}"
    if device_id.startswith("rockpi"):
        digits = "".join(ch for ch in device_id if ch.isdigit())
        if digits:
            return f"rockpi{digits[-1]}"
    reserved = _env_file_value(DEVICE_ENV, "COT_R_RESERVED_IPV4") or ""
    last = reserved.rsplit(".", 1)[-1] if reserved else ""
    return {"195": "rockpi1", "196": "rockpi2", "194": "rockpi3"}.get(last, "rockpi2")


def ssh_ready() -> Dict[str, Any]:
    pub = Path(str(SSH_KEY) + ".pub")
    user = map_user()
    return {
        "key_private": str(SSH_KEY),
        "key_public_file": str(pub),
        "key_present": _exists_file(SSH_KEY),
        "pubkey_present": _exists_file(pub),
        "map_host": MAP_HOST,
        "map_user": user,
        "pubkey": _read_text(pub),
    }


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def ros_quote(value: str) -> str:
    """Quote a value for RouterOS CLI (not POSIX shlex).

    RouterOS expects double quotes for values with spaces. Single quotes from
    shlex.quote break with 'expected end of command'.
    """
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("\n", " ")
        .replace("\r", "")
        .replace("\x00", "")
    )
    return f'"{escaped}"'


def sanitize_ssid(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return (ssid, error). Reject empty, overlong, and control characters."""
    if raw is None:
        return None, "ssid required"
    if not isinstance(raw, str):
        return None, "ssid must be a string"
    # NFC not required; strip NULs/controls that break CLI or HTML.
    cleaned = "".join(ch for ch in raw if 32 <= ord(ch) <= 126 or ord(ch) > 127)
    cleaned = cleaned.strip()
    if not cleaned:
        return None, "ssid empty after sanitizing"
    # Wi-Fi SSIDs are octet-limited; reject obvious oversize UTF-8.
    if len(cleaned.encode("utf-8")) > SSID_MAX_LEN:
        return None, f"ssid longer than {SSID_MAX_LEN} bytes"
    return cleaned, None


def sanitize_psk(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    if raw is None or raw == "":
        return None, None
    if not isinstance(raw, str):
        return None, "psk must be a string"
    cleaned = "".join(ch for ch in raw if ord(ch) >= 32 and ord(ch) != 127)
    if len(cleaned.encode("utf-8")) > PSK_MAX_LEN:
        return None, f"psk longer than {PSK_MAX_LEN} bytes"
    if len(cleaned) < 8:
        return None, "psk must be at least 8 characters for WPA2"
    return cleaned, None


def run_ssh(script: str, *, timeout: int = SSH_TIMEOUT) -> Dict[str, Any]:
    if not _exists_file(SSH_KEY):
        return {
            "ok": False,
            "error": f"SSH key missing at {SSH_KEY}; run prepare and install pubkey on mAP",
        }
    try:
        KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    cmd = [
        "ssh",
        "-i",
        str(SSH_KEY),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        f"ConnectTimeout={min(timeout, 10)}",
        "-o",
        "IdentitiesOnly=yes",
        f"{map_user()}@{MAP_HOST}",
        script,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e), "unreachable": True}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # RouterOS often returns exit 0 even on CLI syntax errors; detect common faults.
    combined = f"{out}\n{err}".lower()
    if "expected end of command" in combined or "syntax error" in combined:
        return {
            "ok": False,
            "error": err or out or "RouterOS syntax error (check SSID quoting)",
            "stdout": out,
            "stderr": err,
        }
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": err or out or f"ssh exit {proc.returncode}",
            "stdout": out,
            "stderr": err,
            "http_status": None,
        }
    return {"ok": True, "stdout": out, "stderr": err}


def _safe_iface(name: str) -> str:
    """Only allow simple RouterOS interface identifiers."""
    if name and re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return name
    return "wlan1"


def scan_ssids(*, duration: int = 6) -> Dict[str, Any]:
    """Scan upstream SSIDs via mAP station radio (RouterOS 7).

    The old compound `scan …; scan print detail without-paging` is invalid and
    yields RouterOS 'expected end of command'. Prefer as-value (ROS7), then a
    plain duration scan whose table stdout we parse.
    """
    duration = max(3, min(int(duration), 15))
    wlan = _safe_iface(WLAN_STA)
    timeout = duration + 20

    # ROS7: returns one key=value;… record per network (ssid may contain spaces).
    as_value_script = (
        f":foreach i in=[/interface wireless scan {wlan} duration={duration} as-value] "
        f"do={{:put $i}}"
    )
    result = run_ssh(as_value_script, timeout=timeout)
    stdout = result.get("stdout") or ""
    networks = _parse_scan(stdout) if result.get("ok") else []

    if not networks:
        # Fallback: interactive duration scan prints a table to SSH stdout.
        # Do not chain a second print; that is what triggered 'expected end of command'.
        plain = run_ssh(
            f"/interface wireless scan {wlan} duration={duration}",
            timeout=timeout,
        )
        if plain.get("ok"):
            stdout = plain.get("stdout") or ""
            networks = _parse_scan(stdout)
            result = plain
        elif not result.get("ok"):
            log.warning(
                "wireless scan failed as-value=%r plain=%r",
                result.get("error"),
                plain.get("error"),
            )
            return {
                "ok": False,
                "error": plain.get("error") or result.get("error") or "scan failed",
                "stdout": plain.get("stdout") or result.get("stdout"),
                "stderr": plain.get("stderr") or result.get("stderr"),
            }

    networks = enrich_networks_with_known(networks)
    return {
        "ok": True,
        "networks": networks,
        "raw_lines": len(stdout.splitlines()),
    }


def _kv_semicolons(line: str) -> Dict[str, str]:
    """Parse RouterOS as-value line: .id=*1;ssid=Cafe WiFi;sig=-55;privacy;…"""
    out: Dict[str, str] = {}
    for m in re.finditer(r"(?:^|;)\.?([a-zA-Z0-9-]+)=([^;]*)", line.strip()):
        out[m.group(1).lower()] = m.group(2)
    # Bare flags (CSV/as-value often emits `privacy` without =value)
    for m in re.finditer(r"(?:^|;)(privacy)(?:=|;|$)", line.strip(), re.I):
        out.setdefault("privacy", "")
    return out


def _classify_security(kv: Dict[str, str]) -> str:
    """Map RouterOS scan fields to open|psk.

    Legacy wireless scan CSV/as-value uses a bare `privacy` flag (often empty
    value) for encrypted APs. Channel may also contain `/P(`.
    """
    raw = (kv.get("privacy") or kv.get("security") or "").strip().lower()
    channel = kv.get("channel") or ""
    if raw in ("no", "none", "open", "false", "0"):
        return "open"
    if (
        raw in ("yes", "true", "1", "privacy")
        or "wpa" in raw
        or "psk" in raw
        or "aes" in raw
        or "tkip" in raw
    ):
        return "psk"
    if re.search(r"/P\(|privacy", channel, re.I):
        return "psk"
    # Key present with empty value (RouterOS flag style) means encrypted.
    if "privacy" in kv and raw == "":
        return "psk"
    if "security" in kv and raw == "":
        return "psk"
    return "open"


def _row_from_kv(kv: Dict[str, str]) -> Optional[Dict[str, Any]]:
    ssid, err = sanitize_ssid(kv.get("ssid"))
    if err or not ssid:
        return None
    row: Dict[str, Any] = {"ssid": ssid}
    bssid = kv.get("address") or kv.get("mac-address") or kv.get("bssid")
    if bssid:
        row["bssid"] = bssid
    for key in ("band", "radio-name", "protocol", "channel"):
        if kv.get(key):
            row[key] = kv[key]
    sig = kv.get("sig") or kv.get("signal") or kv.get("signal-strength")
    if sig is not None:
        try:
            row["signal"] = int(re.sub(r"[^0-9\-]", "", sig) or "0")
        except ValueError:
            row["signal"] = sig
    row["security"] = _classify_security(kv)
    return row


def _parse_scan(text: str) -> List[Dict[str, Any]]:
    """Parse as-value records and/or 'scan print detail' / table blocks."""
    networks: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}

    def flush() -> None:
        nonlocal current
        if current.get("ssid"):
            row = _row_from_kv({k: str(v) for k, v in current.items()})
            if row:
                networks.append(row)
        current = {}

    for line in text.splitlines():
        raw_line = line
        line = line.strip()
        if not line:
            flush()
            continue
        # Skip banners / headers
        if line.startswith("Flags:") or line.startswith("ADDRESS") or "expected end of command" in line.lower():
            continue
        # as-value / terse: one record per line with ssid=…;sig=…
        if "ssid=" in line and ";" in line:
            flush()
            row = _row_from_kv(_kv_semicolons(line))
            if row:
                networks.append(row)
            continue
        # Flags / index line: "0  A ;;; ..." or bare "1"
        if re.match(r"^\d+(\s|$)", line) and "ssid=" not in line and "=" not in line:
            flush()
            continue
        # key=value pairs; quoted values may contain spaces
        for m in re.finditer(r'([a-zA-Z0-9-]+)=("([^"]*)"|(\S+))', line):
            key = m.group(1).lower()
            val = m.group(3) if m.group(3) is not None else m.group(4)
            if key == "ssid":
                current["ssid"] = val
            elif key in ("address", "mac-address"):
                current["bssid"] = val
            elif key in ("band", "radio-name", "channel", "protocol"):
                current[key] = val
            elif key in ("sig", "signal", "signal-strength"):
                current["sig"] = val
            elif key in ("privacy", "security"):
                current["privacy"] = val
        if "ssid=" in line and "ssid" not in current:
            m = re.search(r'ssid="([^"]*)"', line) or re.search(r"ssid=(\S+)", line)
            if m:
                current["ssid"] = m.group(1)
        # Tabular row (ADDRESS SSID …) – only when columns look like a MAC first
        if "ssid" not in current and re.match(
            r"^[A-Z*\s]*([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\s+",
            raw_line.strip(),
        ):
            parts = raw_line.split()
            # flags… ADDRESS SSID BAND FREQ SIG …
            mac_i = next(
                (i for i, p in enumerate(parts) if re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", p)),
                None,
            )
            if mac_i is not None and mac_i + 1 < len(parts):
                flush()
                # SSID may contain spaces: take tokens until a band-like token
                rest = parts[mac_i + 1 :]
                ssid_tokens: List[str] = []
                band = ""
                sig = ""
                for j, tok in enumerate(rest):
                    if re.match(r"^\d+(\.\d+)?(ghz|GHz)", tok) or tok.lower().startswith("2.4") or tok.lower().startswith("5ghz"):
                        band = tok
                        # freq sig often follow
                        if j + 2 < len(rest) and re.match(r"^-?\d+$", rest[j + 2]):
                            sig = rest[j + 2]
                        elif j + 1 < len(rest) and re.match(r"^-?\d+$", rest[j + 1]):
                            sig = rest[j + 1]
                        break
                    ssid_tokens.append(tok)
                flags = " ".join(parts[:mac_i]).upper()
                row_kv = {
                    "ssid": " ".join(ssid_tokens),
                    "address": parts[mac_i],
                    "band": band,
                    "sig": sig,
                }
                if "P" in flags:
                    row_kv["privacy"] = "wpa2-psk"
                row = _row_from_kv(row_kv)
                if row:
                    networks.append(row)
    flush()

    return _dedupe_networks(networks)


def _dedupe_networks(networks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for n in networks:
        ssid = str(n.get("ssid") or "")
        if not ssid:
            continue
        if n.get("security") not in ("open", "psk"):
            # Re-classify from leftover privacy/security/channel fields
            n["security"] = _classify_security(
                {
                    "privacy": str(n.get("privacy") or ""),
                    "security": str(n.get("security") or ""),
                    "channel": str(n.get("channel") or ""),
                }
            )
        # API clients only see open|psk (never raw RouterOS strings)
        n.pop("privacy", None)
        prev = best.get(ssid)
        if not prev:
            best[ssid] = n
            continue
        try:
            if int(n.get("signal") or -999) > int(prev.get("signal") or -999):
                best[ssid] = n
        except (TypeError, ValueError):
            pass
    out = list(best.values())
    out.sort(key=lambda x: int(x.get("signal") or -999), reverse=True)
    return out


def station_config() -> Dict[str, Any]:
    """Read current mAP station SSID and whether the uplink profile has a PSK set.

    The PSK value is returned only for local credential bootstrap; callers must
    not put it in HTTP responses.
    """
    wlan = _safe_iface(WLAN_STA)
    prof = SEC_PROFILE if re.fullmatch(r"[A-Za-z0-9_-]+", SEC_PROFILE) else ros_quote(SEC_PROFILE)
    script = (
        f":put [/interface wireless get {wlan} ssid];"
        f":put [/interface wireless security-profiles get [find name={prof}] "
        f"wpa2-pre-shared-key]"
    )
    result = run_ssh(script, timeout=15)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}
    lines = [ln.strip() for ln in (result.get("stdout") or "").splitlines() if ln.strip()]
    ssid_raw = lines[0] if lines else ""
    psk_raw = lines[1] if len(lines) > 1 else ""
    ssid_s, _ = sanitize_ssid(ssid_raw) if ssid_raw else (None, "empty")
    psk_s = psk_raw if psk_raw and psk_raw.lower() not in ("", "nil", "nothing") else None
    return {
        "ok": True,
        "ssid": ssid_s,
        "has_psk": bool(psk_s),
        "psk": psk_s,
    }


def enrich_networks_with_known(networks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach psk_known / ready flags; bootstrap store from mAP current uplink."""
    from cot_r_mgmt import uplink_creds as creds

    # Seed local store from whatever the mAP already has configured.
    cur = station_config()
    if cur.get("ok") and cur.get("ssid") and cur.get("psk"):
        if not creds.has_psk(str(cur["ssid"])):
            try:
                creds.save_psk(str(cur["ssid"]), str(cur["psk"]))
            except OSError as e:
                log.warning("could not bootstrap uplink creds: %s", e)

    known = creds.known_ssids()
    for n in networks:
        ssid = str(n.get("ssid") or "")
        sec = n.get("security") or "open"
        psk_known = bool(ssid and ssid in known)
        n["psk_known"] = psk_known
        n["ready"] = (sec == "open") or psk_known
        # Never leak credentials in scan payload
        n.pop("psk", None)
    return networks


def connect_uplink(*, ssid: str, psk: Optional[str] = None) -> Dict[str, Any]:
    """Point mAP station profile at SSID; set PSK on security profile when provided."""
    ssid_s, ssid_err = sanitize_ssid(ssid)
    if ssid_err or not ssid_s:
        return {"ok": False, "error": ssid_err or "ssid required"}
    psk_s, psk_err = sanitize_psk(psk)
    if psk_err:
        return {"ok": False, "error": psk_err}

    ssid_q = ros_quote(ssid_s)
    prof = SEC_PROFILE if re.fullmatch(r"[A-Za-z0-9_-]+", SEC_PROFILE) else ros_quote(SEC_PROFILE)
    parts = [
        f"/interface wireless set {WLAN_STA} ssid={ssid_q}",
    ]
    if psk_s:
        # security profile 'uplink' holds the PSK (see docs/networking.md)
        psk_q = ros_quote(psk_s)
        parts.append(
            f"/interface wireless security-profiles set [find name={prof}] "
            f"authentication-types=wpa2-psk mode=dynamic-keys wpa2-pre-shared-key={psk_q}"
        )
        parts.append(f"/interface wireless set {WLAN_STA} security-profile={prof}")
    else:
        parts.append(f"/interface wireless set {WLAN_STA} security-profile={prof}")
    parts.append(f"/interface wireless monitor {WLAN_STA} once")
    script = "; ".join(parts)
    log.info("RouterOS uplink connect ssid=%r script_len=%s", ssid_s, len(script))
    result = run_ssh(script, timeout=25)
    if not result.get("ok"):
        return result
    return {"ok": True, "ssid": ssid_s, "stdout": result.get("stdout"), "detail": "applied on mAP"}
