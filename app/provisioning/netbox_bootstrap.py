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

    await _create_fields(client, content_type, fields_to_create, existing_names)

    # Contacts get a linkage field so the sync can match records both ways.
    contact_ct = "tenancy.contact"
    contact_fields = await client.get_custom_fields(content_type=contact_ct)
    contact_existing = {f["name"] for f in contact_fields}
    await _create_fields(
        client,
        contact_ct,
        [
            {
                "name": "twenty_person_id",
                "type": "text",
                "label": "Twenty Person ID",
                "description": "Linked Twenty CRM Person identifier",
                "filter_logic": "exact",
            },
            {
                "name": "twenty_person_url",
                "type": "url",
                "label": "Twenty Person URL",
                "description": "Deep link to the corresponding Twenty CRM Person",
            },
        ],
        contact_existing,
    )


async def _create_fields(
    client: NetBoxClient,
    content_type: str,
    fields_to_create: list[dict[str, Any]],
    existing_names: set[str],
) -> None:
    for field_def in fields_to_create:
        if field_def["name"] not in existing_names:
            try:
                await client.create_custom_field(
                    {
                        "name": field_def["name"],
                        "object_types": [content_type],
                        "type": field_def["type"],
                        "label": field_def["label"],
                        "description": field_def.get("description", ""),
                        "required": False,
                        "filter_logic": field_def.get("filter_logic", "disabled"),
                    }
                )
                logger.info("Created custom field '%s' on %s", field_def["name"], content_type)
            except Exception as e:
                msg = str(e).lower()
                if any(
                    k in msg for k in ("duplicate", "already exists", "unique constraint", "500")
                ):
                    logger.debug(
                        "Custom field '%s' already exists on %s",
                        field_def["name"],
                        content_type,
                    )
                else:
                    logger.warning(
                        "Failed to create custom field '%s' on %s: %s",
                        field_def["name"],
                        content_type,
                        e,
                    )
        else:
            logger.debug("Custom field '%s' already exists on %s", field_def["name"], content_type)


async def _ensure_event_rule(settings: Settings, client: NetBoxClient) -> None:
    webhook_url = f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/netbox"

    # NetBox v4.7+ stores webhook details in a separate Webhook object.
    webhooks = await client.get_webhooks(name=MIDDLEWARE_MARKER)
    webhook_payload = {
        "name": MIDDLEWARE_MARKER,
        "payload_url": webhook_url,
        "secret": settings.netbox_webhook_secret,
        "ssl_verification": False,
        "http_method": "POST",
        "http_content_type": "application/json",
    }
    if webhooks:
        webhook = webhooks[0]
        needs_update = (
            webhook.get("payload_url") != webhook_url
            or webhook.get("secret") != settings.netbox_webhook_secret
        )
        if needs_update:
            await client.update_webhook(webhook["id"], webhook_payload)
            logger.info("Updated webhook '%s'", MIDDLEWARE_MARKER)
        webhook_id = webhook["id"]
    else:
        webhook = await client.create_webhook(webhook_payload)
        logger.info("Created webhook '%s'", MIDDLEWARE_MARKER)
        webhook_id = webhook["id"]

    # Create the event rule that triggers the webhook.
    rules = await client.get_event_rules(name=MIDDLEWARE_MARKER)
    rule_payload: dict[str, Any] = {
        "name": MIDDLEWARE_MARKER,
        "object_types": ["tenancy.tenant", "tenancy.contact"],
        "event_types": ["object_created", "object_updated", "object_deleted"],
        "action_type": "webhook",
        "action_object_type": "extras.webhook",
        "action_object_id": webhook_id,
    }

    if rules:
        existing = rules[0]
        needs_update = (
            existing.get("action_object_id") != webhook_id
            or set(existing.get("event_types", [])) != set(rule_payload["event_types"])
            or set(existing.get("object_types", [])) != set(rule_payload["object_types"])
        )
        if needs_update:
            await client.update_event_rule(existing["id"], rule_payload)
            logger.info("Updated event rule '%s'", MIDDLEWARE_MARKER)
        else:
            logger.debug("Event rule '%s' already up to date", MIDDLEWARE_MARKER)
    else:
        await client.create_event_rule(rule_payload)
        logger.info("Created event rule '%s'", MIDDLEWARE_MARKER)
