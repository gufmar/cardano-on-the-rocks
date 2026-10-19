# Copyright (C) 2026 Markus. SPDX-License-Identifier: AGPL-3.0-only
"""API token authentication (Bearer). Token never logged."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

ETC_DIR = Path(os.environ.get("COT_R_ETC_DIR", "/etc/cot-r"))
TOKEN_FILE = Path(os.environ.get("COT_R_API_TOKEN_FILE", str(ETC_DIR / "api.token")))
PEER_TOKEN_FILE = Path(os.environ.get("COT_R_PEER_TOKEN_FILE", str(ETC_DIR / "peer.token")))


def ensure_token(path: Path = TOKEN_FILE) -> str:
    """Create a local API token if missing; return current token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    os.chmod(path, 0o640)
    return token


def load_token(path: Path = TOKEN_FILE) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def ensure_peer_token(path: Path = PEER_TOKEN_FILE) -> str:
    """Create shared peer-trust token if missing. Copy the same value to all boards."""
    return ensure_token(path)


def load_peer_token(path: Path = PEER_TOKEN_FILE) -> Optional[str]:
    return load_token(path)


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def token_ok(provided: Optional[str], expected: Optional[str]) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)
