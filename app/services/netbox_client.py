from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("netbox_twenty.netbox_client")

_TRACKING_HEADER = "X-Sync-Source"


class NetBoxClient:
    """Async NetBox REST API client with connection pooling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=f"{settings.netbox_url.rstrip('/')}/api",
            headers={
                "Authorization": f"Token {settings.netbox_token}",
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
        results = await self.get_tenants(custom_field__twenty_company_id=company_id)
        return results[0] if results else None

    async def create_tenant(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/tenancy/tenants/", json=payload)

    async def update_tenant(self, tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/tenancy/tenants/{tenant_id}/", json=payload)

    async def delete_tenant(self, tenant_id: int) -> None:
        await self._delete(f"/tenancy/tenants/{tenant_id}/")

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
