from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.services.twenty_client import TwentyClient

logger = logging.getLogger("netbox_twenty.twenty_bootstrap")

NETBOX_RESOURCE_OBJECT_NAME = "netboxResource"


async def bootstrap_twenty(settings: Settings) -> None:
    """Idempotent startup task: ensure Twenty CRM custom fields,
    custom objects, and webhook subscriptions exist."""
    client = TwentyClient(settings)
    try:
        await _ensure_company_custom_fields(client)
        await _ensure_netbox_resource_object(client)
        await _ensure_webhook(settings, client)
        logger.info("Twenty CRM provisioning complete")
    finally:
        await client.close()


async def _ensure_company_custom_fields(client: TwentyClient) -> None:
    existing_fields = await client.get_company_custom_fields()
    existing_names = {f["name"] for f in existing_fields}

    fields_to_create = [
        {"name": "netboxTenantId", "label": "NetBox Tenant ID", "type": "TEXT", "isRequired": False},
        {"name": "netboxTenantSlug", "label": "NetBox Tenant Slug", "type": "TEXT", "isRequired": False},
        {"name": "netboxTenantUrl", "label": "NetBox Tenant URL", "type": "LINKS", "isRequired": False},
    ]

    for field in fields_to_create:
        if field["name"] not in existing_names:
            await client.create_field("company", {
                "label": field["label"],
                "type": field["type"],
                "isRequired": field["isRequired"],
                "name": field["name"],
            })
            logger.info("Created field '%s' on Company", field["name"])
        else:
            logger.debug("Field '%s' already exists on Company", field["name"])


async def _ensure_netbox_resource_object(client: TwentyClient) -> None:
    meta = await client.get_object_metadata(NETBOX_RESOURCE_OBJECT_NAME)
    if meta is None:
        await client.create_object({
            "nameSingular": NETBOX_RESOURCE_OBJECT_NAME,
            "namePlural": "netboxResources",
            "labelSingular": "NetboxResource",
            "labelPlural": "NetboxResources",
            "description": "Tracks NetBox infrastructure resources (VRFs, Prefixes)",
            "icon": "IconServer",
            "isCustom": True,
            "isActive": True,
            "isSystem": False,
            "fields": [],
        })
        logger.info("Created custom object '%s'", NETBOX_RESOURCE_OBJECT_NAME)
        # Refresh metadata to get the actual field list
        meta = await client.get_object_metadata(NETBOX_RESOURCE_OBJECT_NAME)

    existing_field_names: set[str] = set()
    if meta and "fields" in meta:
        existing_field_names = {f["name"] for f in meta["fields"]}

    fields_to_create = [
        {"name": "name", "label": "Name", "type": "TEXT", "isRequired": True},
        {"name": "type", "label": "Type", "type": "SELECT", "isRequired": True,
         "options": [{"label": "VRF", "value": "VRF"}, {"label": "Prefix", "value": "Prefix"}]},
        {"name": "prefixCidr", "label": "Prefix / CIDR", "type": "TEXT", "isRequired": False},
        {"name": "netboxId", "label": "NetBox ID", "type": "TEXT", "isRequired": True},
        {"name": "companyId", "label": "Company ID", "type": "TEXT", "isRequired": False},
        {"name": "netboxUrl", "label": "NetBox URL", "type": "LINKS", "isRequired": False},
    ]

    for field in fields_to_create:
        if field["name"] not in existing_field_names:
            create_payload: dict[str, Any] = {
                "label": field["label"],
                "type": field["type"],
                "isRequired": field["isRequired"],
            }
            if "options" in field:
                create_payload["options"] = field["options"]
            await client.create_object_field(NETBOX_RESOURCE_OBJECT_NAME, create_payload)
            logger.info("Created field '%s' on %s", field["name"], NETBOX_RESOURCE_OBJECT_NAME)
        else:
            logger.debug("Field '%s' already exists on %s", field["name"], NETBOX_RESOURCE_OBJECT_NAME)


async def _ensure_webhook(settings: Settings, client: TwentyClient) -> None:
    webhook_url = f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/twenty"
    target_token = settings.twenty_webhook_token

    webhooks = await client.get_webhooks()
    sync_webhooks = [
        w for w in webhooks
        if w.get("targetUrl") == webhook_url or w.get("description", "") == "NetBox Twenty Middleware"
    ]

    webhook_payload: dict[str, Any] = {
        "targetUrl": webhook_url,
        "operations": ["company.created", "company.updated", "company.deleted"],
        "isActive": True,
        "description": "NetBox Twenty Middleware",
        "headers": {
            "Authorization": f"Bearer {target_token}",
        },
    }

    if sync_webhooks:
        existing = sync_webhooks[0]
        needs_update = (
            existing.get("targetUrl") != webhook_url
            or not existing.get("isActive")
            or existing.get("headers", {}).get("Authorization") != f"Bearer {target_token}"
        )
        if needs_update:
            await client.update_webhook(existing["id"], webhook_payload)
            logger.info("Updated Twenty webhook subscription")
        else:
            logger.debug("Twenty webhook already up to date")
    else:
        await client.create_webhook(webhook_payload)
        logger.info("Created Twenty webhook subscription")
