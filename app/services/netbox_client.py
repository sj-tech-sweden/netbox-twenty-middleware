from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("netbox_twenty.netbox_client")

_TRACKING_HEADER = "X-Sync-Source"


def _netbox_auth_header(token: str) -> tuple[str, str]:
    """Return the correct Authorization header scheme and value for a NetBox API token.

    NetBox v4.7+ uses hashed v2 tokens sent as ``Bearer nbt_<key>.<secret>``.
    Older NetBox versions use plaintext v1 tokens sent as ``Token <secret>``.
    """
    if token.startswith("nbt_"):
        return "Bearer", token
    return "Token", token


class NetBoxClient:
    """Async NetBox REST API client with connection pooling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        scheme, credentials = _netbox_auth_header(settings.netbox_token)
        self._client = httpx.AsyncClient(
            base_url=f"{settings.netbox_url.rstrip('/')}/api",
            headers={
                "Authorization": f"{scheme} {credentials}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                _TRACKING_HEADER: settings.sync_source_value,
            },
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: dict[str, Any]) -> Any:
        resp = await self._client.post(path, json=json)
        resp.raise_for_status()
        return resp.json()

    async def _patch(self, path: str, json: dict[str, Any]) -> Any:
        resp = await self._client.patch(path, json=json)
        resp.raise_for_status()
        return resp.json()

    async def _put(self, path: str, json: dict[str, Any]) -> Any:
        resp = await self._client.put(path, json=json)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str) -> None:
        resp = await self._client.delete(path)
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Custom Fields
    # ------------------------------------------------------------------

    async def get_custom_fields(self, content_type: str) -> list[dict[str, Any]]:
        data = await self._get("/extras/custom-fields/", params={"content_type": content_type})
        return data.get("results", [])

    async def create_custom_field(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/extras/custom-fields/", json=payload)

    # ------------------------------------------------------------------
    # Tenants
    # ------------------------------------------------------------------

    async def get_tenants(self, **kwargs: Any) -> list[dict[str, Any]]:
        data = await self._get("/tenancy/tenants/", params=kwargs)
        return data.get("results", [])

    async def get_tenant(self, tenant_id: int) -> dict[str, Any]:
        return await self._get(f"/tenancy/tenants/{tenant_id}/")

    async def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        results = await self.get_tenants(slug=slug)
        return results[0] if results else None

    async def get_tenant_by_company_id(self, company_id: str) -> dict[str, Any] | None:
        results = await self.get_tenants(cf_twenty_company_id=company_id)
        return results[0] if results else None

    async def create_tenant(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/tenancy/tenants/", json=payload)

    async def update_tenant(self, tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/tenancy/tenants/{tenant_id}/", json=payload)

    async def delete_tenant(self, tenant_id: int) -> None:
        await self._delete(f"/tenancy/tenants/{tenant_id}/")

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def get_contacts(self, **kwargs: Any) -> list[dict[str, Any]]:
        data = await self._get("/tenancy/contacts/", params=kwargs)
        return data.get("results", [])

    async def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        try:
            return await self._get(f"/tenancy/contacts/{contact_id}/")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_contact_by_person_id(self, person_id: str) -> dict[str, Any] | None:
        results = await self.get_contacts(cf_twenty_person_id=person_id)
        return results[0] if results else None

    async def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/tenancy/contacts/", json=payload)

    async def update_contact(self, contact_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/tenancy/contacts/{contact_id}/", json=payload)

    async def delete_contact(self, contact_id: int) -> None:
        await self._delete(f"/tenancy/contacts/{contact_id}/")

    async def get_contact_assignments(
        self, contact_id: int | None = None, object_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Return contact assignments, optionally filtered by contact and object type."""
        params: dict[str, Any] = {}
        if contact_id is not None:
            params["contact_id"] = contact_id
        if object_type is not None:
            params["object_type"] = object_type
        data = await self._get("/tenancy/contact-assignments/", params=params)
        return data.get("results", [])

    async def _ensure_contact_role(self, name: str = "CRM Sync") -> int:
        data = await self._get("/tenancy/contact-roles/", params={"name": name})
        results = data.get("results", [])
        if results:
            return results[0]["id"]
        created = await self._post(
            "/tenancy/contact-roles/", json={"name": name, "slug": "crm-sync"}
        )
        return created["id"]

    async def delete_contact_assignment(self, assignment_id: int) -> None:
        await self._delete(f"/tenancy/contact-assignments/{assignment_id}/")

    async def create_contact_assignment(
        self, contact_id: int, object_type: str, object_id: int | str
    ) -> dict[str, Any]:
        role_id = await self._ensure_contact_role()
        return await self._post(
            "/tenancy/contact-assignments/",
            json={
                "contact": contact_id,
                "object_type": object_type,
                "object_id": object_id,
                "role": role_id,
            },
        )

    # ------------------------------------------------------------------
    # VRFs
    # ------------------------------------------------------------------

    async def get_vrfs(self, **kwargs: Any) -> list[dict[str, Any]]:
        data = await self._get("/ipam/vrfs/", params=kwargs)
        return data.get("results", [])

    async def get_vrf(self, vrf_id: int) -> dict[str, Any]:
        return await self._get(f"/ipam/vrfs/{vrf_id}/")

    async def create_vrf(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/ipam/vrfs/", json=payload)

    async def update_vrf(self, vrf_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/ipam/vrfs/{vrf_id}/", json=payload)

    # ------------------------------------------------------------------
    # Prefixes
    # ------------------------------------------------------------------

    async def get_prefixes(self, **kwargs: Any) -> list[dict[str, Any]]:
        data = await self._get("/ipam/prefixes/", params=kwargs)
        return data.get("results", [])

    async def get_prefix(self, prefix_id: int) -> dict[str, Any]:
        return await self._get(f"/ipam/prefixes/{prefix_id}/")

    async def create_prefix(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/ipam/prefixes/", json=payload)

    async def update_prefix(self, prefix_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/ipam/prefixes/{prefix_id}/", json=payload)

    # ------------------------------------------------------------------
    # Webhooks (NetBox v4.x uses Event Rules)
    # ------------------------------------------------------------------

    async def get_webhooks(self, **kwargs: Any) -> list[dict[str, Any]]:
        data = await self._get("/extras/webhooks/", params=kwargs)
        return data.get("results", [])

    async def create_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/extras/webhooks/", json=payload)

    async def update_webhook(self, webhook_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/extras/webhooks/{webhook_id}/", json=payload)

    async def get_event_rules(self, **kwargs: Any) -> list[dict[str, Any]]:
        data = await self._get("/extras/event-rules/", params=kwargs)
        return data.get("results", [])

    async def create_event_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/extras/event-rules/", json=payload)

    async def update_event_rule(self, rule_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/extras/event-rules/{rule_id}/", json=payload)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def is_healthy(self) -> bool:
        try:
            resp = await self._client.get("/status/")
            return resp.status_code == 200
        except Exception:
            return False
