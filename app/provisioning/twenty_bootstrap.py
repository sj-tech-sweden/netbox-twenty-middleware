from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.services.twenty_client import TwentyClient

logger = logging.getLogger("netbox_twenty.twenty_bootstrap")

NETBOX_RESOURCE_OBJECT_NAME = "netboxresource"


async def bootstrap_twenty(settings: Settings) -> None:
    """Idempotent startup task: ensure Twenty CRM custom fields,
    custom objects, and webhook subscriptions exist."""
    client = TwentyClient(settings)
    try:
        await _ensure_company_custom_fields(client)
        await _ensure_person_custom_fields(client)
        await _ensure_netbox_resource_object(client)
        await _ensure_webhook(settings, client)
        logger.info("Twenty CRM provisioning complete")
    finally:
        await client.close()


async def _ensure_company_custom_fields(client: TwentyClient) -> None:
    # Get the Company object metadata ID
    company_object_id = await client.get_company_object_id()
    if not company_object_id:
        logger.warning(
            "Could not find Company object metadata - skipping custom field provisioning"
        )
        return

    existing_fields = await client.get_company_custom_fields()
    existing_names = {f["name"] for f in existing_fields}

    fields_to_create = [
        {
            "name": "netboxTenantId",
            "label": "NetBox Tenant ID",
            "type": "TEXT",
            "description": "NetBox Tenant identifier",
        },
        {
            "name": "netboxTenantSlug",
            "label": "NetBox Tenant Slug",
            "type": "TEXT",
            "description": "NetBox Tenant URL slug",
        },
        {
            "name": "netboxTenantUrl",
            "label": "NetBox Tenant URL",
            "type": "LINKS",
            "description": "Deep link to the NetBox Tenant",
        },
    ]

    for field in fields_to_create:
        if field["name"] not in existing_names:
            try:
                await client.create_field(
                    company_object_id,
                    {
                        "name": field["name"],
                        "label": field["label"],
                        "type": field["type"],
                        "description": field.get("description", ""),
                        "isNullable": True,
                        "isActive": True,
                        "isUIEditable": True,
                    },
                )
                logger.info("Created field '%s' on Company", field["name"])
            except Exception as e:
                # Field may already exist from a previous failed attempt
                if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                    logger.debug("Field '%s' already exists on Company", field["name"])
                else:
                    logger.warning(
                        "Failed to create field '%s' on Company: %s",
                        field["name"],
                        e,
                    )
        else:
            logger.debug("Field '%s' already exists on Company", field["name"])


async def _ensure_person_custom_fields(client: TwentyClient) -> None:
    person_object = await client.get_object_metadata("person")
    if not person_object:
        logger.warning("Could not find Person object metadata - skipping person provisioning")
        return

    person_object_id = person_object["id"]
    existing_names = {f["name"] for f in person_object.get("fields", [])}

    fields_to_create = [
        {
            "name": "netboxContactId",
            "label": "NetBox Contact ID",
            "type": "TEXT",
            "description": "NetBox Contact identifier",
        },
        {
            "name": "netboxContactUrl",
            "label": "NetBox Contact URL",
            "type": "LINKS",
            "description": "Deep link to the NetBox Contact",
        },
    ]

    for field in fields_to_create:
        if field["name"] not in existing_names:
            try:
                await client.create_field(
                    person_object_id,
                    {
                        "name": field["name"],
                        "label": field["label"],
                        "type": field["type"],
                        "description": field.get("description", ""),
                        "isNullable": True,
                        "isActive": True,
                        "isUIEditable": True,
                    },
                )
                logger.info("Created field '%s' on Person", field["name"])
            except Exception as e:
                if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                    logger.debug("Field '%s' already exists on Person", field["name"])
                else:
                    logger.warning(
                        "Failed to create field '%s' on Person: %s",
                        field["name"],
                        e,
                    )
        else:
            logger.debug("Field '%s' already exists on Person", field["name"])


async def _ensure_netbox_resource_object(client: TwentyClient) -> None:
    meta = await client.get_object_metadata(NETBOX_RESOURCE_OBJECT_NAME)
    if meta is None:
        result = await client.create_object(
            {
                "nameSingular": "netboxresource",
                "namePlural": "netboxresources",
                "labelSingular": "NetboxResource",
                "labelPlural": "NetboxResources",
                "description": "Tracks NetBox infrastructure resources (VRFs, Prefixes)",
                "icon": "IconServer",
                "isLabelSyncedWithName": True,
            }
        )
        object_id = result.get("id")
        if not object_id:
            logger.error("Failed to create custom object '%s'", NETBOX_RESOURCE_OBJECT_NAME)
            return
        logger.info("Created custom object '%s' (id=%s)", NETBOX_RESOURCE_OBJECT_NAME, object_id)
        # Refresh metadata to get the actual field list
        meta = await client.get_object_metadata(NETBOX_RESOURCE_OBJECT_NAME)
    else:
        object_id = meta["id"]

    existing_field_names: set[str] = set()
    if meta and "fields" in meta:
        existing_field_names = {f["name"] for f in meta["fields"]}

    fields_to_create = [
        {
            "name": "name",
            "label": "Name",
            "type": "TEXT",
            "isRequired": True,
        },
        {
            "name": "resourcetype",
            "label": "Resource Type",
            "type": "SELECT",
            "isRequired": True,
            "options": [
                {"label": "VRF", "value": "VRF", "position": 0, "color": "blue"},
                {"label": "Prefix", "value": "PREFIX", "position": 1, "color": "green"},
            ],
        },
        {
            "name": "prefixcidr",
            "label": "Prefix / CIDR",
            "type": "TEXT",
            "isRequired": False,
        },
        {
            "name": "netboxid",
            "label": "NetBox ID",
            "type": "TEXT",
            "isRequired": True,
        },
        {
            "name": "companyid",
            "label": "Company ID",
            "type": "TEXT",
            "isRequired": False,
        },
        {
            "name": "netboxurl",
            "label": "NetBox URL",
            "type": "LINKS",
            "isRequired": False,
        },
    ]

    for field in fields_to_create:
        if field["name"] not in existing_field_names:
            create_payload: dict[str, Any] = {
                "name": field["name"],
                "label": field["label"],
                "type": field["type"],
                "isNullable": not field.get("isRequired", False),
                "isActive": True,
                "isUIEditable": True,
            }
            if "options" in field:
                create_payload["options"] = field["options"]
            try:
                await client.create_field(object_id, create_payload)
                logger.info("Created field '%s' on %s", field["name"], NETBOX_RESOURCE_OBJECT_NAME)
            except Exception as e:
                # Field may already exist from a previous failed attempt
                if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                    logger.debug(
                        "Field '%s' already exists on %s",
                        field["name"],
                        NETBOX_RESOURCE_OBJECT_NAME,
                    )
                else:
                    logger.warning(
                        "Failed to create field '%s' on %s: %s",
                        field["name"],
                        NETBOX_RESOURCE_OBJECT_NAME,
                        e,
                    )
        else:
            logger.debug(
                "Field '%s' already exists on %s",
                field["name"],
                NETBOX_RESOURCE_OBJECT_NAME,
            )


async def _ensure_webhook(settings: Settings, client: TwentyClient) -> None:
    webhook_url = f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/twenty"
    target_token = settings.twenty_webhook_token

    webhooks = await client.get_webhooks()
    sync_webhooks = [
        w
        for w in webhooks
        if w.get("targetUrl") == webhook_url
        or w.get("description", "") == "NetBox Twenty Middleware"
    ]

    webhook_payload: dict[str, Any] = {
        "targetUrl": webhook_url,
        "operations": [
            "company.created",
            "company.updated",
            "company.deleted",
            "person.created",
            "person.updated",
            "person.deleted",
        ],
        "description": "NetBox Twenty Middleware",
        "secret": target_token,
    }

    if sync_webhooks:
        existing = sync_webhooks[0]
        needs_update = (
            existing.get("targetUrl") != webhook_url
            or existing.get("secret") != target_token
            or set(existing.get("operations", [])) != set(webhook_payload["operations"])
        )
        if needs_update:
            await client.update_webhook(existing["id"], webhook_payload)
            logger.info("Updated Twenty webhook subscription")
        else:
            logger.debug("Twenty webhook already up to date")
    else:
        await client.create_webhook(webhook_payload)
        logger.info("Created Twenty webhook subscription")
