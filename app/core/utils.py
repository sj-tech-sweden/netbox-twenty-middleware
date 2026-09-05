from __future__ import annotations

import re
import unicodedata


_SLUGIFY_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_MULTI_DASH_RE = re.compile(r"[-\s]+", re.UNICODE)


def slugify(value: str, max_length: int = 64) -> str:
    """Convert a string to a URL-friendly slug.

    Handles unicode, collapses whitespace/dashes, and truncates to *max_length*.
    """
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = _SLUGIFY_RE.sub("", value)
    value = _MULTI_DASH_RE.sub("-", value)
    value = value.strip("-").lower()
    if len(value) > max_length:
        value = value[:max_length].rstrip("-")
    return value


def sanitize_netbox_tenant_name(name: str) -> str:
    """Strip control characters and trim whitespace for NetBox tenant names."""
    return "".join(c for c in name if unicodedata.category(c)[0] != "C").strip()


def extract_model_name(target: str) -> str | None:
    """Extract the app.model part from a NetBox event target like 'tenancy.tenant'."""
    parts = target.split(".")
    return parts[1] if len(parts) == 2 else None
