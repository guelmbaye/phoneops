"""Telnyx webhook authenticity.

The endpoint answers calls and speaks on a real phone line. Anyone who can post
to it can make it talk, so the signature is not optional decoration — but it is
verified only when a public key is configured, so the harness still runs against
a local tunnel during setup.
"""

from __future__ import annotations

import base64
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

#: Telnyx signs `timestamp|body` with Ed25519.
TOLERANCE_SECONDS = 300


def verify(
    *, public_key: str, signature: str | None, timestamp: str | None, body: bytes
) -> tuple[bool, str]:
    """Returns (ok, reason). Reason is empty when the signature checks out."""
    if not signature or not timestamp:
        return False, "missing telnyx-signature-ed25519 or telnyx-timestamp header"

    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False, "telnyx-timestamp is not an integer"
    if age > TOLERANCE_SECONDS:
        return False, f"timestamp is {int(age)}s old, outside the {TOLERANCE_SECONDS}s window"

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(base64.b64decode(signature), f"{timestamp}|".encode() + body)
    except (InvalidSignature, ValueError, TypeError) as exc:
        return False, f"signature rejected: {type(exc).__name__}"
    return True, ""
