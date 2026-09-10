from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.services.netbox_client import NetBoxClient
from app.services.twenty_client import TwentyClient


def make_resp(status=200, json_data=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    r.raise_for_status = MagicMock()
    return r


def make_error_resp(status=404):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {}
    req = MagicMock()
    r.request = req
    r.raise_for_status.side_effect = httpx.HTTPStatusError("err", request=req, response=r)
    return r


def base_settings(**overrides):
    s = Settings(
        netbox_url="http://netbox:8000",
        twenty_url="http://twenty:3000",
        valkey_host="localhost",
        valkey_port=6379,
        public_base_url="http://localhost:8000",
        netbox_webhook_secret="netbox-secret",
        twenty_webhook_token="twenty-token",
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# NetBox client
# ---------------------------------------------------------------------------


@pytest.fixture
def nb_client():
    with patch("app.services.netbox_client.httpx.AsyncClient") as cls:
        inner = MagicMock()
        for verb in ("get", "post", "patch", "put", "delete"):
            setattr(inner, verb, AsyncMock())
        inner.aclose = AsyncMock()
        cls.return_value = inner
        settings = base_settings()
        client = NetBoxClient(settings)
        yield client, inner, cls, settings


class TestNetBoxClient:
    def test_auth_header_bearer_for_v2_token(self):
        with patch("app.services.netbox_client.httpx.AsyncClient") as cls:
            NetBoxClient(base_settings(netbox_token="nbt_abc.secret"))
            headers = cls.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer nbt_abc.secret"

    def test_auth_header_token_for_v1_token(self):
        with patch("app.services.netbox_client.httpx.AsyncClient") as cls:
            NetBoxClient(base_settings(netbox_token="plain-secret"))
            headers = cls.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Token plain-secret"

    @pytest.mark.anyio
    async def test_tenants(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_resp(200, {"results": [{"id": 1}]})
        result = await client.get_tenants(slug="t")
        assert result == [{"id": 1}]
        inner.get.assert_called_with("/tenancy/tenants/", params={"slug": "t"})

    @pytest.mark.anyio
    async def test_tenant_crud(self, nb_client):
        client, inner, _, _ = nb_client
        inner.post.return_value = make_resp(200, {"id": 1})
        await client.create_tenant({"name": "T"})
        inner.post.assert_called_with("/tenancy/tenants/", json={"name": "T"})

        inner.get.return_value = make_resp(200, {"id": 1})
        await client.get_tenant(1)
        inner.get.assert_called_with("/tenancy/tenants/1/", params=None)

        inner.patch.return_value = make_resp(200, {"id": 1})
        await client.update_tenant(1, {"name": "X"})
        inner.patch.assert_called_with("/tenancy/tenants/1/", json={"name": "X"})

        inner.delete.return_value = make_resp(204)
        await client.delete_tenant(1)
        inner.delete.assert_called_with("/tenancy/tenants/1/")

    @pytest.mark.anyio
    async def test_contacts(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_resp(200, {"results": [{"id": 2}]})
        result = await client.get_contacts()
        assert result == [{"id": 2}]

        inner.get.return_value = make_resp(200, {"id": 2})
        await client.get_contact(2)
        inner.get.assert_called_with("/tenancy/contacts/2/", params=None)

    @pytest.mark.anyio
    async def test_contact_get_404_returns_none(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_error_resp(404)
        result = await client.get_contact(99)
        assert result is None

    @pytest.mark.anyio
    async def test_contact_assignments_and_role(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_resp(200, {"results": [{"id": 7}]})
        await client.get_contact_assignments(contact_id=1)
        inner.get.assert_called_with("/tenancy/contact-assignments/", params={"contact_id": 1})

        inner.get.return_value = make_resp(200, {"results": [{"id": 5}]})
        inner.post.return_value = make_resp(200, {"id": 8})
        await client.create_contact_assignment(1, "dcim.device", 2)
        _, kwargs = inner.post.call_args
        assert kwargs["json"]["role"] == 5
        assert kwargs["json"]["contact"] == 1
        assert kwargs["json"]["object_type"] == "dcim.device"

        inner.delete.return_value = make_resp(204)
        await client.delete_contact_assignment(9)
        inner.delete.assert_called_with("/tenancy/contact-assignments/9/")

    @pytest.mark.anyio
    async def test_vrf_and_prefix(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_resp(200, {"results": []})
        await client.get_vrfs()
        inner.get.assert_called_with("/ipam/vrfs/", params={})
        await client.get_prefixes()
        inner.get.assert_called_with("/ipam/prefixes/", params={})

    @pytest.mark.anyio
    async def test_webhooks_and_event_rules(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_resp(200, {"results": []})
        await client.get_webhooks()
        inner.get.assert_called_with("/extras/webhooks/", params={})
        await client.get_event_rules()
        inner.get.assert_called_with("/extras/event-rules/", params={})

    @pytest.mark.anyio
    async def test_custom_fields(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_resp(200, {"results": []})
        await client.get_custom_fields("tenancy.tenant")
        inner.get.assert_called_with(
            "/extras/custom-fields/", params={"content_type": "tenancy.tenant"}
        )
        inner.post.return_value = make_resp(200, {"id": 1})
        await client.create_custom_field({"name": "x"})
        inner.post.assert_called_with("/extras/custom-fields/", json={"name": "x"})

    @pytest.mark.anyio
    async def test_health(self, nb_client):
        client, inner, _, _ = nb_client
        inner.get.return_value = make_resp(200)
        assert await client.is_healthy() is True
        inner.get.return_value = make_resp(500)
        assert await client.is_healthy() is False
        inner.get.side_effect = httpx.ConnectError("boom")
        assert await client.is_healthy() is False

    @pytest.mark.anyio
    async def test_close(self, nb_client):
        client, inner, _, _ = nb_client
        inner.aclose = AsyncMock()
        await client.close()
        inner.aclose.assert_awaited()


# ---------------------------------------------------------------------------
# Twenty client
# ---------------------------------------------------------------------------


@pytest.fixture
def tw_client():
    with patch("app.services.twenty_client.httpx.AsyncClient") as cls:
        inner = MagicMock()
        for verb in ("get", "post", "patch", "put", "delete"):
            setattr(inner, verb, AsyncMock())
        inner.aclose = AsyncMock()
        cls.return_value = inner
        settings = base_settings()
        client = TwentyClient(settings)
        yield client, inner, cls, settings


class TestTwentyClient:
    def test_auth_header(self):
        with patch("app.services.twenty_client.httpx.AsyncClient") as cls:
            TwentyClient(base_settings(twenty_api_key="test-api-key"))
            headers = cls.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-api-key"

    @pytest.mark.anyio
    async def test_company_get_unwraps(self, tw_client):
        client, inner, _, _ = tw_client
        inner.get.return_value = make_resp(200, {"data": {"company": {"id": "c1"}}})
        result = await client.get_company("c1")
        assert result == {"id": "c1"}
        inner.get.assert_called_with("/rest/companies/c1", params=None)

    @pytest.mark.anyio
    async def test_company_get_404(self, tw_client):
        client, inner, _, _ = tw_client
        inner.get.return_value = make_error_resp(404)
        assert await client.get_company("x") is None

    @pytest.mark.anyio
    async def test_company_list_and_crud(self, tw_client):
        client, inner, _, _ = tw_client
        inner.get.return_value = make_resp(200, {"data": {"companies": [{"id": "c1"}]}})
        result = await client.get_companies()
        assert result == [{"id": "c1"}]

        inner.post.return_value = make_resp(200, {"data": {"createCompany": {"id": "c1"}}})
        result = await client.create_company({"name": "X"})
        assert result == {"id": "c1"}
        inner.post.assert_called_with("/rest/companies", json={"name": "X"})

        inner.patch.return_value = make_resp(200, {"data": {"updateCompany": {"id": "c1"}}})
        await client.update_company("c1", {"name": "Y"})
        inner.patch.assert_called_with("/rest/companies/c1", json={"name": "Y"})

    @pytest.mark.anyio
    async def test_person_crud(self, tw_client):
        client, inner, _, _ = tw_client
        inner.get.return_value = make_resp(200, {"data": {"people": [{"id": "p1"}]}})
        result = await client.get_people()
        assert result == [{"id": "p1"}]

        inner.post.return_value = make_resp(200, {"data": {"createPerson": {"id": "p1"}}})
        result = await client.create_person({"name": {}})
        assert result == {"id": "p1"}
        inner.post.assert_called_with("/rest/people", json={"name": {}})

    @pytest.mark.anyio
    async def test_metadata_graphql_selection(self, tw_client):
        client, inner, _, _ = tw_client
        inner.post.return_value = make_resp(200, {"data": {"objects": {"edges": []}}})
        await client._metadata_graphql("query X")
        inner.post.assert_called_with("/metadata", json={"query": "query X"})
        await client._metadata_graphql("query Y", {"a": 1})
        inner.post.assert_called_with("/metadata", json={"query": "query Y", "variables": {"a": 1}})

    @pytest.mark.anyio
    async def test_webhooks(self, tw_client):
        client, inner, _, _ = tw_client
        inner.post.return_value = make_resp(200, {"data": {"webhooks": [{"id": "w1"}]}})
        result = await client.get_webhooks()
        assert result == [{"id": "w1"}]
        inner.post.assert_called_once()
        assert inner.post.call_args.args[0] == "/metadata"

    @pytest.mark.anyio
    async def test_netbox_resources(self, tw_client):
        client, inner, _, _ = tw_client
        inner.get.return_value = make_resp(200, {"data": {"netboxresources": [{"id": "r1"}]}})
        result = await client.get_netbox_resources()
        assert result == [{"id": "r1"}]
        inner.get.assert_called_with("/rest/netboxresources", params={})

        inner.post.return_value = make_resp(200, {"data": {"createNetboxresource": {"id": "r1"}}})
        result = await client.create_netbox_resource({"name": "x"})
        assert result == {"id": "r1"}
        inner.post.assert_called_with("/rest/netboxresources", json={"name": "x"})

    @pytest.mark.anyio
    async def test_health(self, tw_client):
        client, inner, _, _ = tw_client
        inner.get.return_value = make_resp(200)
        assert await client.is_healthy() is True
        inner.get.return_value = make_resp(500)
        assert await client.is_healthy() is False

    @pytest.mark.anyio
    async def test_close(self, tw_client):
        client, inner, _, _ = tw_client
        inner.aclose = AsyncMock()
        await client.close()
        inner.aclose.assert_awaited()
