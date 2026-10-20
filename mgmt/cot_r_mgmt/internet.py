# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""Best-effort Internet reachability probes (no captive-portal login)."""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

# Multiple targets; any success counts as "internet likely available".
HTTP_PROBES: List[Tuple[str, str]] = [
    ("http://detectportal.firefox.com/success.txt", "success"),
    ("http://connectivitycheck.gstatic.com/generate_204", ""),
]
DNS_NAME = "one.one.one.one"
TCP_FALLBACK = ("1.1.1.1", 443)


def probe(timeout: float = 3.0) -> Dict[str, Any]:
    details: List[str] = []
    dns_ok = False
    try:
        socket.getaddrinfo(DNS_NAME, 443, type=socket.SOCK_STREAM)
        dns_ok = True
        details.append("dns_ok")
    except OSError as e:
        details.append(f"dns_fail:{e}")

    http_ok = False
    for url, expect in HTTP_PROBES:
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "cot-r/1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(64).decode("utf-8", errors="replace")
                if expect:
                    if expect in body:
                        http_ok = True
                        details.append(f"http_ok:{url}")
                        break
                elif resp.status in (200, 204):
                    http_ok = True
                    details.append(f"http_ok:{url}")
                    break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            details.append(f"http_fail:{url}:{e}")

    tcp_ok = False
    try:
        with socket.create_connection(TCP_FALLBACK, timeout=timeout):
            tcp_ok = True
            details.append("tcp_ok:1.1.1.1:443")
    except OSError as e:
        details.append(f"tcp_fail:{e}")

    ok = bool(http_ok or (dns_ok and tcp_ok))
    return {
        "ok": ok,
        "dns_ok": dns_ok,
        "http_ok": http_ok,
        "tcp_ok": tcp_ok,
        "detail": "; ".join(details[-6:]),
    }
