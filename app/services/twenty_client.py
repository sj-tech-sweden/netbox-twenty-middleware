from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("netbox_twenty.twenty_client")

_TRACKING_HEADER = "X-Sync-Source"


class TwentyClient:
    """Async Twenty CRM REST / GraphQL client with connection pooling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.twenty_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={
                "Authorization": f"Bearer {settings.twenty_api_key}",
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
    # Generic REST helpers
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

    async def _delete(self, path: str) -> None:
        resp = await self._client.delete(path)
        resp.raise_for_status()

    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = await self._client.post("/graphql", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    async def get_company(self, company_id: str) -> dict[str, Any] | None:
        try:
            return await self._get(f"/rest/companies/{company_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_companies(self, **kwargs: Any) -> list[dict[str, Any]]:
        data = await self._get("/rest/companies", params=kwargs)
        return data.get("data", [])

    async def create_company(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/rest/companies", json=payload)

    async def update_company(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/rest/companies/{company_id}", json=payload)

    # ------------------------------------------------------------------
    # Custom Fields (on Company)
    # ------------------------------------------------------------------

    async def get_company_custom_fields(self) -> list[dict[str, Any]]:
        data = await self._get("/rest/metadata/fieldMetadata", params={"objectName": "company"})
        return data.get("data", [])

    async def create_field(self, object_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/rest/metadata/fieldMetadata", json={
            "objectName": object_name,
            **payload,
        })

    # ------------------------------------------------------------------
    # Custom Object Definitions
    # ------------------------------------------------------------------

    async def get_object_metadata(self, object_name: str) -> dict[str, Any] | None:
        try:
            return await self._get(f"/rest/metadata/objectMetadata/{object_name}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def create_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/rest/metadata/objectMetadata", json=payload)

    async def create_object_field(self, object_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            f"/rest/metadata/objectMetadata/{object_name}/fields",
            json=payload,
        )

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    async def get_webhooks(self) -> list[dict[str, Any]]:
        data = await self._get("/rest/webhooks")
        return data.get("data", [])

    async def create_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/rest/webhooks", json=payload)

    async def update_webhook(self, webhook_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/rest/webhooks/{webhook_id}", json=payload)

    # ------------------------------------------------------------------
    # Custom Object Records (NetboxResource)
    # ------------------------------------------------------------------

    async def get_netbox_resources(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = filters or {}
        data = await self._get("/rest/netboxResources", params=params)
        return data.get("data", [])

    async def create_netbox_resource(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/rest/netboxResources", json=payload)

    async def update_netbox_resource(self, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/rest/netboxResources/{record_id}", json=payload)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def is_healthy(self) -> bool:
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False
