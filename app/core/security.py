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
