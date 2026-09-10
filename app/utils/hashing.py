from __future__ import annotations

import hashlib
import json


def generate_state_signature(tool_name: str, payload: dict[str, object]) -> str:
    """Generate SHA-256 hex of tool_name + canonical JSON payload."""
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    content = f"{tool_name}{canonical_json}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def hash_body(body: bytes) -> str:
    """Return SHA-256 hex digest of raw body bytes."""
    return hashlib.sha256(body).hexdigest()
