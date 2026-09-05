from __future__ import annotations

import json
import logging
from typing import Any

from app.config import Settings
from app.core.utils import slugify
from app.services.netbox_client import NetBoxClient
from app.services.twenty_client import TwentyClient

logger = logging.getLogger("netbox_twenty.sync_engine")


class SyncEngine:
    """Processes webhook events and synchronises between NetBox and Twenty."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._netbox = NetBoxClient(settings)
        self._twenty = TwentyClient(settings)

    async def close(self) -> None:
        await self._netbox.close()
        await self._twenty.close()

    # ------------------------------------------------------------------
    # Twenty → NetBox
    # ------------------------------------------------------------------

    async def handle_twenty_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("event", "")
        data = payload.get("data", {})

        match event_type:
            case "company.created" | "company.updated":
                await self._sync_company_to_tenant(data)
            case "company.deleted":
                await self._delete_tenant_for_company(data)
            case _:
                logger.debug("Ignoring unhandled Twenty event: %s", event_type)

    def _twenty_company_url(self, company_id: str) -> str:
        base = self._settings.twenty_url.rstrip("/")
        return f"{base}/object/company/{company_id}"

    def _netbox_tenant_url(self, tenant_id: str) -> str:
        base = self._settings.netbox_url.rstrip("/")
        return f"{base}/tenancy/tenants/{tenant_id}/"

    async def _sync_company_to_tenant(self, company: dict[str, Any]) -> None:
        company_id = company.get("id")
        company_name = company.get("name", "Unnamed")
        slug = slugify(company_name)
        custom_fields = company.get("customFields", {})
        existing_tenant_id = custom_fields.get("netbox_tenantId")
        company_url = self._twenty_company_url(company_id)

        # If we already have a NetBox tenant ID on the company, try to update it
        if existing_tenant_id:
            try:
                tenant = await self._netbox.get_tenant(int(existing_tenant_id))
            except Exception:
                logger.warning("Stale netbox_tenant_id %s on company %s", existing_tenant_id, company_id)
                tenant = None

            if tenant:
                # Idempotency: check if update is needed
                update_payload: dict[str, Any] = {}
                if tenant.get("name") != company_name:
                    update_payload["name"] = company_name
                if tenant.get("slug") != slug:
                    update_payload["slug"] = slug

                cf = tenant.get("custom_fields", {})
                if cf.get("twenty_company_id") != company_id:
                    update_payload.setdefault("custom_fields", {})["twenty_company_id"] = company_id
                if cf.get("twenty_company_url") != company_url:
                    update_payload.setdefault("custom_fields", {})["twenty_company_url"] = company_url

                if update_payload:
                    await self._netbox.update_tenant(int(existing_tenant_id), update_payload)
                    logger.info("Updated NetBox tenant %s from company %s", existing_tenant_id, company_id)
                else:
                    logger.debug("Tenant %s already in sync for company %s", existing_tenant_id, company_id)
                return

        # No existing tenant – create one
        create_payload = {
            "name": company_name,
            "slug": slug,
            "custom_fields": {
                "twenty_company_id": company_id,
                "twenty_company_url": company_url,
            },
        }
        new_tenant = await self._netbox.create_tenant(create_payload)
        new_tenant_id = str(new_tenant["id"])
        netbox_url = self._netbox_tenant_url(new_tenant_id)

        # Write back the NetBox tenant ID and deeplink to Twenty
        await self._twenty.update_company(company_id, {
            "customFields": {
                "netbox_tenantId": new_tenant_id,
                "netbox_tenantSlug": slug,
                "netboxTenantUrl": netbox_url,
            },
        })
        logger.info("Created NetBox tenant %s for company %s", new_tenant_id, company_id)

    async def _delete_tenant_for_company(self, company: dict[str, Any]) -> None:
        custom_fields = company.get("customFields", {})
        tenant_id = custom_fields.get("netbox_tenantId")
        if not tenant_id:
            return
        try:
            await self._netbox.delete_tenant(int(tenant_id))
            logger.info("Deleted NetBox tenant %s for company %s", tenant_id, company.get("id"))
        except Exception:
            logger.exception("Failed to delete NetBox tenant %s", tenant_id)

    # ------------------------------------------------------------------
    # NetBox → Twenty
    # ------------------------------------------------------------------

    async def handle_netbox_event(self, payload: dict[str, Any]) -> None:
        model = payload.get("model", "")
        action = payload.get("action", "")
        data = payload.get("data", {})

        match model:
            case "tenant":
                await self._sync_tenant_to_company(action, data)
            case "vrf" | "prefix":
                await self._sync_infra_to_netbox_resource(model, action, data)
            case _:
                logger.debug("Ignoring unhandled NetBox model: %s", model)

    async def _sync_tenant_to_company(self, action: str, tenant: dict[str, Any]) -> None:
        company_id = (tenant.get("custom_fields") or {}).get("twenty_company_id")

        if action == "deleted":
            if company_id:
                await self._twenty.update_company(company_id, {
                    "customFields": {
                        "netbox_tenantId": None,
                        "netbox_tenantSlug": None,
                        "netboxTenantUrl": None,
                    },
                })
                logger.info("Cleared NetBox IDs on company %s after tenant deletion", company_id)
            return

        tenant_name = tenant.get("name", "")
        tenant_slug = tenant.get("slug", "")
        tenant_id = str(tenant.get("id", ""))
        tenant_url = self._netbox_tenant_url(tenant_id)

        if company_id:
            # Idempotency check
            existing = await self._twenty.get_company(company_id)
            if existing:
                cf = existing.get("customFields", {})
                needs_update = (
                    cf.get("netbox_tenantId") != tenant_id
                    or cf.get("netbox_tenantSlug") != tenant_slug
                    or cf.get("netboxTenantUrl") != tenant_url
                )
                if needs_update:
                    await self._twenty.update_company(company_id, {
                        "customFields": {
                            "netbox_tenantId": tenant_id,
                            "netbox_tenantSlug": tenant_slug,
                            "netboxTenantUrl": tenant_url,
                        },
                    })
                    logger.info("Updated company %s with NetBox tenant %s", company_id, tenant_id)
                else:
                    logger.debug("Company %s already synced with tenant %s", company_id, tenant_id)
            return

        # No company linked – find or create by slug
        companies = await self._twenty.get_companies(slug=tenant_slug)
        if companies:
            existing_company = companies[0]
            cid = existing_company["id"]
            await self._twenty.update_company(cid, {
                "customFields": {
                    "netbox_tenantId": tenant_id,
                    "netbox_tenantSlug": tenant_slug,
                    "netboxTenantUrl": tenant_url,
                },
            })
            logger.info("Linked existing company %s to NetBox tenant %s", cid, tenant_id)
        else:
            new_company = await self._twenty.create_company({
                "name": tenant_name,
                "domainName": "",
                "customFields": {
                    "netbox_tenantId": tenant_id,
                    "netbox_tenantSlug": tenant_slug,
                    "netboxTenantUrl": tenant_url,
                },
            })
            # Write back the company ID and deeplink to NetBox
            company_url = self._twenty_company_url(new_company["id"])
            await self._netbox.update_tenant(tenant.get("id"), {
                "custom_fields": {
                    "twenty_company_id": new_company["id"],
                    "twenty_company_url": company_url,
                },
            })
            logger.info("Created company %s for NetBox tenant %s", new_company["id"], tenant_id)

    def _netbox_resource_url(self, model: str, resource_id: str) -> str:
        base = self._settings.netbox_url.rstrip("/")
        if model == "vrf":
            return f"{base}/ipam/vrfs/{resource_id}/"
        return f"{base}/ipam/prefixes/{resource_id}/"

    async def _sync_infra_to_netbox_resource(
        self, model: str, action: str, resource: dict[str, Any]
    ) -> None:
        """Sync VRF/Prefix → Twenty NetboxResource custom object."""
        resource_type = "VRF" if model == "vrf" else "Prefix"
        netbox_id = str(resource.get("id", ""))
        tenant_id = resource.get("tenant", {})
        if isinstance(tenant_id, dict):
            tenant_id = str(tenant_id.get("id", ""))

        netbox_url = self._netbox_resource_url(model, netbox_id)

        # Find existing record
        existing = await self._twenty.get_netbox_resources(filters={"netboxId": netbox_id})
        existing_record = existing[0] if existing else None

        if action == "deleted":
            if existing_record:
                await self._twenty._delete(f"/rest/netboxResources/{existing_record['id']}")
                logger.info("Deleted NetboxResource %s", existing_record["id"])
            return

        name = resource.get("name") or resource.get("prefix") or resource.get("cidr") or f"{resource_type}-{netbox_id}"
        prefix_cidr = resource.get("prefix") or resource.get("cidr") or ""

        record_data: dict[str, Any] = {
            "name": name,
            "type": resource_type,
            "prefixCidr": prefix_cidr,
            "netboxId": netbox_id,
            "companyId": tenant_id,
            "netboxUrl": netbox_url,
        }

        if existing_record:
            # Idempotency
            changed = any(existing_record.get(k) != v for k, v in record_data.items() if k != "companyId")
            if changed:
                await self._twenty.update_netbox_resource(existing_record["id"], record_data)
                logger.info("Updated NetboxResource %s", existing_record["id"])
        else:
            await self._twenty.create_netbox_resource(record_data)
            logger.info("Created NetboxResource for %s %s", resource_type, netbox_id)


# ---------------------------------------------------------------------------
# Worker task entry points (called by saq workers)
# ---------------------------------------------------------------------------

def _load_settings() -> Settings:
    return Settings()


async def process_twenty_event(ctx: dict[str, Any], job: dict[str, Any]) -> None:
    """saq task: process a Twenty CRM webhook event."""
    engine = SyncEngine(_load_settings())
    try:
        await engine.handle_twenty_event(job["payload"])
    finally:
        await engine.close()


async def process_netbox_event(ctx: dict[str, Any], job: dict[str, Any]) -> None:
    """saq task: process a NetBox webhook event."""
    engine = SyncEngine(_load_settings())
    try:
        await engine.handle_netbox_event(job["payload"])
    finally:
        await engine.close()
