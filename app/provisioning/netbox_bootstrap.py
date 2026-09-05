from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.services.netbox_client import NetBoxClient

logger = logging.getLogger("netbox_twenty.netbox_bootstrap")

MIDDLEWARE_MARKER = "Twenty-CRM-Sync"


async def bootstrap_netbox(settings: Settings) -> None:
    """Idempotent startup task: ensure NetBox custom fields and event rules exist."""
    client = NetBoxClient(settings)
    try:
        await _ensure_custom_fields(client, settings)
        await _ensure_event_rule(settings, client)
        logger.info("NetBox provisioning complete")
    finally:
        await client.close()


async def _ensure_custom_fields(client: NetBoxClient, settings: Settings) -> None:
    content_type = "tenancy.tenant"
    fields = await client.get_custom_fields(content_type=content_type)
    existing_names = {f["name"] for f in fields}

    fields_to_create = [
        {
            "name": "twenty_company_id",
            "type": "text",
            "label": "Twenty Company ID",
            "description": "Linked Twenty CRM Company identifier",
            "filter_logic": "exact",
        },
        {
            "name": "twenty_company_url",
            "type": "url",
            "label": "Twenty Company URL",
            "description": "Deep link to the corresponding Twenty CRM Company",
        },
    ]

    for field_def in fields_to_create:
        if field_def["name"] not in existing_names:
            await client.create_custom_field({
                "name": field_def["name"],
                "content_types": [content_type],
                "type": field_def["type"],
                "label": field_def["label"],
                "description": field_def["description"],
                "required": False,
                "filter_logic": field_def.get("filter_logic", "disabled"),
            })
            logger.info("Created custom field '%s' on %s", field_def["name"], content_type)
        else:
            logger.debug("Custom field '%s' already exists on %s", field_def["name"], content_type)


async def _ensure_event_rule(settings: Settings, client: NetBoxClient) -> None:
    webhook_url = f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/netbox"
    rules = await client.get_event_rules(name=MIDDLEWARE_MARKER)

    rule_payload: dict[str, Any] = {
        "name": MIDDLEWARE_MARKER,
        "event_create": True,
        "event_update": True,
        "event_delete": True,
        "event_approve": False,
        "event_stage_change": False,
        "event_job_update": False,
        "object_types": ["tenancy.tenant"],
        "action_type": "webhook",
        "action_data": {
            "url": webhook_url,
            "secret_key": settings.netbox_webhook_secret,
            "additional_data": {},
        },
    }

    if rules:
        existing = rules[0]
        needs_update = (
            existing.get("action_data", {}).get("url") != webhook_url
            or existing.get("action_data", {}).get("secret_key") != settings.netbox_webhook_secret
        )
        if needs_update:
            await client.update_event_rule(existing["id"], rule_payload)
            logger.info("Updated event rule '%s'", MIDDLEWARE_MARKER)
        else:
            logger.debug("Event rule '%s' already up to date", MIDDLEWARE_MARKER)
    else:
        await client.create_event_rule(rule_payload)
        logger.info("Created event rule '%s'", MIDDLEWARE_MARKER)
