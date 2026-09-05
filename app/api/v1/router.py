from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response

from app.config import Settings
from app.core.valkey import get_valkey_client
from app.services.netbox_client import NetBoxClient

router = APIRouter(prefix="/v1", tags=["api"])

_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


@router.get("/healthz", status_code=200)
async def healthz() -> Response:
    return Response(content='{"status":"ok"}', media_type="application/json")


@router.get("/readyz", status_code=200)
async def readyz() -> Response:
    settings = _get_settings()
    checks: dict[str, str] = {}

    # Check Valkey
    try:
        client = await get_valkey_client(settings.valkey_host, settings.valkey_port)
        await client.ping()
        checks["valkey"] = "ok"
    except Exception:
        checks["valkey"] = "error"

    # Check NetBox
    try:
        nb = NetBoxClient(settings)
        healthy = await nb.is_healthy()
        checks["netbox"] = "ok" if healthy else "error"
        await nb.close()
    except Exception:
        checks["netbox"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return Response(
        content=f'{{"status":"{"ok" if all_ok else "degraded"}","checks":{checks}}}',
        media_type="application/json",
        status_code=status_code,
    )
