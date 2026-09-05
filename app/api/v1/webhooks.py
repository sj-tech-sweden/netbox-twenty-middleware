from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.config import Settings
from app.core.security import verify_netbox_signature, verify_twenty_token
from app.core.valkey import get_valkey_client, enqueue_event

logger = logging.getLogger("netbox_twenty.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


@router.post("/twenty", status_code=202)
async def handle_twenty_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = None,
) -> Response:
    settings = _get_settings()

    # Verify bearer token or query param token
    auth_token = None
    if authorization and authorization.startswith("Bearer "):
        auth_token = authorization[7:]
    elif token:
        auth_token = token

    if not auth_token or not verify_twenty_token(auth_token, settings.twenty_webhook_token):
        logger.warning("Twenty webhook: invalid or missing token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    event_type = body.get("event", "unknown")
    logger.info("Twenty webhook received: event=%s", event_type)

    client = await get_valkey_client(settings.valkey_host, settings.valkey_port)
    enqueue_event(client, "twenty_events", body)

    return Response(
        content=json.dumps({"status": "accepted", "event": event_type}),
        media_type="application/json",
        status_code=202,
    )


@router.post("/netbox", status_code=202)
async def handle_netbox_webhook(
    request: Request,
    x_hook_signature: str | None = Header(default=None),
) -> Response:
    settings = _get_settings()

    raw_body = await request.body()

    # Verify HMAC SHA-512 signature
    if not x_hook_signature:
        logger.warning("NetBox webhook: missing X-Hook-Signature header")
        raise HTTPException(status_code=401, detail="Missing signature")

    if not verify_netbox_signature(raw_body, x_hook_signature, settings.netbox_webhook_secret):
        logger.warning("NetBox webhook: invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    body = json.loads(raw_body)
    model = body.get("model", "unknown")
    action = body.get("action", "unknown")
    logger.info("NetBox webhook received: model=%s action=%s", model, action)

    client = await get_valkey_client(settings.valkey_host, settings.valkey_port)
    enqueue_event(client, "netbox_events", body)

    return Response(
        content=json.dumps({"status": "accepted", "model": model, "action": action}),
        media_type="application/json",
        status_code=202,
    )
