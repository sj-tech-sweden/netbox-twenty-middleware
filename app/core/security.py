from __future__ import annotations

import hashlib
import hmac


def verify_netbox_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC SHA-512 signature sent by NetBox webhooks."""
    expected = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_twenty_token(token: str, secret: str) -> bool:
    """Verify Bearer / query-parameter token from Twenty CRM webhooks."""
    return hmac.compare_digest(token, secret)


def verify_twenty_signature(
    raw_body: bytes,
    signature: str | None,
    timestamp: str | None,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify the HMAC-SHA256 signature Twenty CRM sends with webhooks.

    Twenty computes the signature as::

        HMAC-SHA256(secret, f"{timestamp}:{json_payload}")

    and sends it in the ``X-Twenty-Webhook-Signature`` header together with
    ``X-Twenty-Webhook-Timestamp``. The secret is never sent in the body.
    """
    if not signature or not timestamp:
        return False
    try:
        # Twenty sends the timestamp in milliseconds (Date.now()).
        age = abs(int(__import__("time").time() * 1000) - int(timestamp))
    except ValueError, TypeError:
        return False
    if age > max_age_seconds * 1000:
        return False
    expected = hmac.new(
        secret.encode(),
        f"{timestamp}:".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
