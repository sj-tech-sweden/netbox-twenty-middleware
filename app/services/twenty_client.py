from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("netbox_twenty.twenty_client")

_TRACKING_HEADER = "X-Sync-Source"

# Twenty enforces a rate limit (100 tokens / 60s). When hit we back off and
# retry instead of letting the whole reconciliation abort.
_MAX_RATE_LIMIT_RETRIES = 6


class TwentyRateLimitError(Exception):
    """Raised when Twenty keeps rate-limiting after the retry budget is spent."""


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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """HTTP request with retry/backoff on rate limiting (429) and 5xx."""
        attempt = 0
        while True:
            resp = await self._client.request(method, path, json=json, params=params)
            status = resp.status_code
            # Only retry on rate limiting or transient server errors.
            if status not in (429, 500, 502, 503, 504):
                return resp
            if attempt >= _MAX_RATE_LIMIT_RETRIES:
                return resp
            attempt += 1
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                delay = float(retry_after)
            else:
                delay = min(2**attempt, 30) + random.uniform(0, 1)
            logger.warning(
                "Twenty API rate limited/errored (%s); backing off %.1fs (attempt %d/%d)",
                status,
                delay,
                attempt,
                _MAX_RATE_LIMIT_RETRIES,
            )
            await asyncio.sleep(delay)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._request("GET", path, params=params)
        await self._check_status(resp)
        return resp.json()

    async def _post(self, path: str, json: dict[str, Any]) -> Any:
        resp = await self._request("POST", path, json=json)
        await self._check_status(resp)
        return resp.json()

    async def _patch(self, path: str, json: dict[str, Any]) -> Any:
        resp = await self._request("PATCH", path, json=json)
        await self._check_status(resp)
        return resp.json()

    async def _delete(self, path: str) -> None:
        resp = await self._request("DELETE", path)
        await self._check_status(resp)

    @staticmethod
    async def _check_status(resp) -> None:
        if resp.is_success:
            return
        body = resp.text
        request = getattr(resp, "request", None)
        method = request.method if request else "?"
        url = request.url if request else "?"
        logger.error(
            "Twenty API %s %s failed (%s): %s",
            method,
            url,
            resp.status_code,
            body[:2000],
        )
        resp.raise_for_status()

    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = await self._request("POST", "/graphql", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    async def get_company(self, company_id: str) -> dict[str, Any] | None:
        try:
            result = await self._get(f"/rest/companies/{company_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        data = result.get("data")
        if isinstance(data, dict):
            return data.get("company") if isinstance(data.get("company"), dict) else data
        return None

    async def get_companies(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._get_all_rest("/rest/companies", "companies", kwargs)

    async def create_company(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._post("/rest/companies", json=payload)
        data = result.get("data", {})
        return data.get("createCompany", data) if isinstance(data, dict) else {}

    async def update_company(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._patch(f"/rest/companies/{company_id}", json=payload)
        data = result.get("data", {})
        return data.get("updateCompany", data) if isinstance(data, dict) else {}

    async def delete_company(self, company_id: str) -> None:
        await self._delete(f"/rest/companies/{company_id}")

    async def get_company_by_tenant_id(self, tenant_id: str) -> dict[str, Any] | None:
        companies = await self.get_companies()
        for company in companies:
            if str(company.get("netboxTenantId")) == str(tenant_id):
                return company
        return None

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------

    async def get_person(self, person_id: str) -> dict[str, Any] | None:
        try:
            result = await self._get(f"/rest/people/{person_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        data = result.get("data")
        if isinstance(data, dict):
            return data.get("person") if isinstance(data.get("person"), dict) else None
        return None

    async def get_people(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._get_all_rest("/rest/people", "people", kwargs)

    async def _get_all_rest(
        self, path: str, key: str, extra_params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Page through a REST list endpoint (cursor pagination, limit 200)."""
        items: list[dict[str, Any]] = []
        params = dict(extra_params or {})
        params["limit"] = 200
        after: str | None = None
        while True:
            if after is not None:
                params["startingAfter"] = after
            result = await self._get(path, params=params)
            data = result.get("data") or {}
            page_items = data.get(key, []) if isinstance(data, dict) else []
            if not isinstance(page_items, list):
                page_items = []
            items.extend(page_items)
            if not page_items:
                break
            page_info = result.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            if not after:
                break
        return items

    async def create_person(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._post("/rest/people", json=payload)
        data = result.get("data", {})
        return data.get("createPerson", data) if isinstance(data, dict) else {}

    async def update_person(self, person_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._patch(f"/rest/people/{person_id}", json=payload)
        data = result.get("data", {})
        return data.get("updatePerson", data) if isinstance(data, dict) else {}

    async def delete_person(self, person_id: str) -> None:
        await self._delete(f"/rest/people/{person_id}")

    async def get_person_by_contact_id(self, contact_id: str) -> dict[str, Any] | None:
        """Find a person by its linked NetBox contact id.

        Uses GraphQL (not the REST list) because custom fields like
        ``netboxContactId`` are reliably returned/filterable there, and the REST
        list is paginated (default 60) so a naive scan can miss records and
        cause duplicate-creation attempts.
        """
        query = """
        query PersonByNetboxContactId($filter: PersonFilter!) {
          people(filter: $filter, paging: {first: 1}) {
            edges {
              node {
                id
                name { firstName lastName }
                emails { primaryEmail }
                phones { primaryPhoneNumber }
                netboxContactId
                netboxContactUrl
                companyId
              }
            }
          }
        }
        """
        try:
            data = await self._graphql(
                query, {"filter": {"netboxContactId": {"eq": str(contact_id)}}}
            )
        except Exception:
            return None
        people = (data.get("data", {}) or {}).get("people", {}) or {}
        edges = people.get("edges", []) if isinstance(people, dict) else []
        if edges:
            return edges[0].get("node")
        return None

    # ------------------------------------------------------------------
    # Metadata API (GraphQL)
    # ------------------------------------------------------------------

    async def _metadata_graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = await self._request("POST", "/metadata", json=payload)
        resp.raise_for_status()
        result = resp.json()
        if "errors" in result:
            raise Exception(f"GraphQL errors: {result['errors']}")
        return result.get("data", {})

    # ------------------------------------------------------------------
    # Custom Fields (on Company)
    # ------------------------------------------------------------------

    async def _get_all_objects(self) -> list[dict[str, Any]]:
        """Page through all objectMetadata entries.

        The metadata list API enforces a paging limit, so we must walk every
        page to reliably find objects (e.g. ``netboxresource``) that may not
        appear on the first page in a workspace with many standard objects.
        """
        objects: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            query = """
            query GetAllObjects($after: ConnectionCursor) {
                objects(paging: {first: 100, after: $after}) {
                    edges {
                        node {
                            id
                            nameSingular
                            namePlural
                            labelSingular
                            labelPlural
                            description
                            icon
                            isActive
                            isSystem
                            fields(paging: {first: 100}) {
                                edges {
                                    node {
                                        id
                                        name
                                        label
                                        type
                                        isSystem
                                    }
                                }
                            }
                        }
                    }
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                }
            }
            """
            variables = {"after": after} if after else {}
            data = await self._metadata_graphql(query, variables)
            objs = data.get("objects", {})
            for edge in objs.get("edges", []):
                objects.append(edge["node"])
            page_info = objs.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            if not after:
                break
        return objects

    async def get_company_object_id(self) -> str | None:
        """Get the objectMetadata ID for the 'company' object."""
        for obj in await self._get_all_objects():
            if obj.get("nameSingular", "").lower() == "company":
                return obj["id"]
        return None

    async def get_company_custom_fields(self) -> list[dict[str, Any]]:
        """Get custom fields on the Company object."""
        object_id = await self.get_company_object_id()
        if not object_id:
            return []

        query = """
        query GetFields($filter: FieldFilter!) {
            fields(filter: $filter, paging: {first: 100}) {
                edges {
                    node {
                        id
                        name
                        isSystem
                        applicationId
                    }
                }
            }
        }
        """
        data = await self._metadata_graphql(
            query, {"filter": {"objectMetadataId": {"eq": object_id}}}
        )
        # Custom fields are non-system fields. In this Twenty version, user-created
        # custom fields carry an applicationId (the "custom" app), so we must NOT
        # exclude them by applicationId. We rely on name matching in the bootstrap
        # to decide which ones belong to us.
        return [
            edge["node"]
            for edge in data.get("fields", {}).get("edges", [])
            if not edge["node"].get("isSystem")
        ]

    async def create_field(
        self, object_metadata_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a custom field on an object via metadata GraphQL API."""
        mutation = """
        mutation CreateOneField($input: CreateOneFieldMetadataInput!) {
            createOneField(input: $input) {
                id
                name
            }
        }
        """
        data = await self._metadata_graphql(
            mutation,
            {
                "input": {
                    "field": {
                        "objectMetadataId": object_metadata_id,
                        **payload,
                    }
                }
            },
        )
        return data.get("createOneField", {})

    # ------------------------------------------------------------------
    # Custom Object Definitions
    # ------------------------------------------------------------------

    async def get_object_metadata(self, object_name: str) -> dict[str, Any] | None:
        """Get object metadata by name (case-insensitive), paging all objects."""
        for obj in await self._get_all_objects():
            if obj.get("nameSingular", "").lower() == object_name.lower():
                # Flatten fields for easier access
                obj["fields"] = [f["node"] for f in obj.get("fields", {}).get("edges", [])]
                return obj
        return None

    async def create_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a custom object via metadata GraphQL API."""
        mutation = """
        mutation CreateOneObject($input: CreateOneObjectInput!) {
            createOneObject(input: $input) {
                id
                nameSingular
            }
        }
        """
        data = await self._metadata_graphql(mutation, {"input": {"object": payload}})
        return data.get("createOneObject", {})

    async def create_object_field(
        self,
        object_metadata_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a field on a custom object via metadata GraphQL API."""
        return await self.create_field(object_metadata_id, payload)

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    async def get_webhooks(self) -> list[dict[str, Any]]:
        """Get all webhooks via metadata GraphQL API."""
        query = """
        query GetWebhooks {
            webhooks {
                id
                targetUrl
                operations
                description
                secret
            }
        }
        """
        data = await self._metadata_graphql(query)
        return data.get("webhooks", [])

    async def create_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a webhook via metadata GraphQL API."""
        mutation = """
        mutation CreateWebhook($input: CreateWebhookInput!) {
            createWebhook(input: $input) {
                id
                targetUrl
            }
        }
        """
        data = await self._metadata_graphql(mutation, {"input": payload})
        return data.get("createWebhook", {})

    async def update_webhook(self, webhook_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a webhook via metadata GraphQL API.

        Twenty's ``updateWebhook`` mutation expects the changed fields nested
        under an ``update`` key rather than at the top level of the input.
        """
        mutation = """
        mutation UpdateWebhook($input: UpdateWebhookInput!) {
            updateWebhook(input: $input) {
                id
                targetUrl
            }
        }
        """
        data = await self._metadata_graphql(
            mutation,
            {
                "input": {
                    "id": webhook_id,
                    "update": payload,
                }
            },
        )
        return data.get("updateWebhook", {})

    # ------------------------------------------------------------------
    # Custom Object Records (NetboxResource)
    # ------------------------------------------------------------------

    async def get_netbox_resources(
        self,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._get_all_rest("/rest/netboxresources", "netboxresources", filters or {})

    async def create_netbox_resource(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._post("/rest/netboxresources", json=payload)
        data = result.get("data", {})
        return data.get("createNetboxresource", data) if isinstance(data, dict) else {}

    async def update_netbox_resource(
        self,
        record_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self._patch(f"/rest/netboxresources/{record_id}", json=payload)
        data = result.get("data", {})
        return data.get("updateNetboxresource", data) if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def is_healthy(self) -> bool:
        try:
            resp = await self._request("GET", "/health")
            return resp.status_code == 200
        except Exception:
            return False
