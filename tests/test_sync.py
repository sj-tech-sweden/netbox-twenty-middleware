from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.core.utils import extract_model_name, sanitize_netbox_tenant_name, slugify
from app.services.sync_engine import SyncEngine


@pytest.fixture
def settings():
    return Settings(
        netbox_url="http://netbox:8000",
        netbox_token="test-token",
        twenty_url="http://twenty:3000",
        twenty_api_key="test-api-key",
        valkey_host="localhost",
        valkey_port=6379,
        public_base_url="http://localhost:8000",
        netbox_webhook_secret="netbox-secret",
        twenty_webhook_token="twenty-token",
    )


@pytest.fixture
def engine(settings):
    with (
        patch("app.services.sync_engine.NetBoxClient") as MockNB,
        patch("app.services.sync_engine.TwentyClient") as MockTwenty,
    ):
        nb = MockNB.return_value
        twenty = MockTwenty.return_value
        eng = SyncEngine(settings)
        eng._netbox = nb
        eng._twenty = twenty
        yield eng, nb, twenty


# ------------------------------------------------------------------
# Twenty → NetBox sync tests
# ------------------------------------------------------------------

class TestTwentyToNetbox:
    @pytest.mark.anyio
    async def test_company_created_creates_tenant(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.return_value = None
        nb.create_tenant.return_value = {"id": 42}

        await eng._sync_company_to_tenant({"id": "comp-1", "name": "Acme Corp"})

        nb.create_tenant.assert_called_once()
        call_args = nb.create_tenant.call_args[0][0]
        assert call_args["name"] == "Acme Corp"
        assert call_args["slug"] == "acme-corp"
        assert call_args["custom_fields"]["twenty_company_id"] == "comp-1"
        assert call_args["custom_fields"]["twenty_company_url"] == "http://twenty:3000/object/company/comp-1"

        twenty.update_company.assert_called_once()
        update_args = twenty.update_company.call_args[0]
        assert update_args[0] == "comp-1"
        assert update_args[1]["customFields"]["netboxTenantUrl"] == "http://netbox:8000/tenancy/tenants/42/"

    @pytest.mark.anyio
    async def test_company_created_with_existing_tenant_id_updates(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.return_value = {
            "id": 42,
            "name": "Old Name",
            "slug": "old-name",
            "custom_fields": {"twenty_company_id": "comp-1", "twenty_company_url": "old-url"},
        }

        await eng._sync_company_to_tenant({"id": "comp-1", "name": "New Name"})

        nb.update_tenant.assert_called_once()
        patch_args = nb.update_tenant.call_args[0]
        assert patch_args[0] == 42
        assert patch_args[1]["name"] == "New Name"
        assert patch_args[1]["slug"] == "new-name"
        assert patch_args[1]["custom_fields"]["twenty_company_url"] == "http://twenty:3000/object/company/comp-1"

    @pytest.mark.anyio
    async def test_company_updated_patches_tenant(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.return_value = {
            "id": 42,
            "name": "Old Name",
            "slug": "old-name",
            "custom_fields": {"twenty_company_id": "comp-1"},
        }

        await eng._sync_company_to_tenant({
            "id": "comp-1",
            "name": "New Name",
        })

        nb.update_tenant.assert_called_once()
        patch_args = nb.update_tenant.call_args[0]
        assert patch_args[0] == 42
        assert patch_args[1]["name"] == "New Name"
        assert patch_args[1]["slug"] == "new-name"

    @pytest.mark.anyio
    async def test_company_updated_skips_if_identical(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.return_value = {
            "id": 42,
            "name": "Acme Corp",
            "slug": "acme-corp",
            "custom_fields": {
                "twenty_company_id": "comp-1",
                "twenty_company_url": "http://twenty:3000/object/company/comp-1",
            },
        }

        await eng._sync_company_to_tenant({
            "id": "comp-1",
            "name": "Acme Corp",
        })

        nb.update_tenant.assert_not_called()

    @pytest.mark.anyio
    async def test_company_updated_stale_tenant_id_creates_new(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.side_effect = Exception("404 Not Found")
        nb.create_tenant.return_value = {"id": 99}

        await eng._sync_company_to_tenant({"id": "comp-1", "name": "Acme Corp"})

        nb.create_tenant.assert_called_once()
        assert nb.create_tenant.call_args[0][0]["custom_fields"]["twenty_company_id"] == "comp-1"

    @pytest.mark.anyio
    async def test_company_deleted_removes_tenant(self, engine):
        eng, nb, twenty = engine
        company = {
            "id": "comp-1",
            "customFields": {"netbox_tenantId": "42"},
        }
        await eng._delete_tenant_for_company(company)
        nb.delete_tenant.assert_called_once_with(42)

    @pytest.mark.anyio
    async def test_company_deleted_noop_without_tenant_id(self, engine):
        eng, nb, twenty = engine
        await eng._delete_tenant_for_company({"id": "comp-1", "customFields": {}})
        nb.delete_tenant.assert_not_called()

    @pytest.mark.anyio
    async def test_company_deleted_handles_netbox_error(self, engine):
        eng, nb, twenty = engine
        nb.delete_tenant.side_effect = Exception("NetBox error")
        # Should not raise
        await eng._delete_tenant_for_company({
            "id": "comp-1",
            "customFields": {"netbox_tenantId": "42"},
        })

    @pytest.mark.anyio
    async def test_unhandled_event_type_ignored(self, engine):
        eng, nb, twenty = engine
        await eng.handle_twenty_event({"event": "unknown.event", "data": {}})
        nb.create_tenant.assert_not_called()
        nb.update_tenant.assert_not_called()


# ------------------------------------------------------------------
# NetBox → Twenty sync tests
# ------------------------------------------------------------------

class TestNetboxToTwenty:
    @pytest.mark.anyio
    async def test_tenant_created_links_to_existing_company(self, engine):
        eng, nb, twenty = engine
        tenant = {
            "id": 10,
            "name": "Acme Corp",
            "slug": "acme-corp",
            "custom_fields": {"twenty_company_id": None},
        }
        twenty.get_companies.return_value = [{"id": "comp-1"}]
        twenty.get_company.return_value = {"id": "comp-1", "customFields": {}}

        await eng._sync_tenant_to_company("created", tenant)

        twenty.update_company.assert_called_once()
        args = twenty.update_company.call_args[0]
        assert args[0] == "comp-1"
        assert args[1]["customFields"]["netbox_tenantId"] == "10"
        assert args[1]["customFields"]["netboxTenantUrl"] == "http://netbox:8000/tenancy/tenants/10/"

    @pytest.mark.anyio
    async def test_tenant_created_skips_if_already_synced(self, engine):
        eng, nb, twenty = engine
        tenant = {
            "id": 10,
            "name": "Acme Corp",
            "slug": "acme-corp",
            "custom_fields": {"twenty_company_id": "comp-1"},
        }
        twenty.get_company.return_value = {
            "id": "comp-1",
            "customFields": {
                "netbox_tenantId": "10",
                "netboxTenantUrl": "http://netbox:8000/tenancy/tenants/10/",
            },
        }

        await eng._sync_tenant_to_company("created", tenant)

        twenty.update_company.assert_not_called()

    @pytest.mark.anyio
    async def test_tenant_created_updates_url_when_changed(self, engine):
        eng, nb, twenty = engine
        tenant = {
            "id": 10,
            "name": "Acme Corp",
            "slug": "acme-corp",
            "custom_fields": {"twenty_company_id": "comp-1"},
        }
        twenty.get_company.return_value = {
            "id": "comp-1",
            "customFields": {"netbox_tenantId": "10", "netboxTenantUrl": "old-url"},
        }

        await eng._sync_tenant_to_company("created", tenant)

        twenty.update_company.assert_called_once()
        args = twenty.update_company.call_args[0]
        assert args[1]["customFields"]["netboxTenantUrl"] == "http://netbox:8000/tenancy/tenants/10/"

    @pytest.mark.anyio
    async def test_tenant_created_creates_company_when_not_found(self, engine):
        eng, nb, twenty = engine
        tenant = {
            "id": 10,
            "name": "Acme Corp",
            "slug": "acme-corp",
            "custom_fields": {},
        }
        twenty.get_companies.return_value = []
        twenty.create_company.return_value = {"id": "comp-new"}

        await eng._sync_tenant_to_company("created", tenant)

        twenty.create_company.assert_called_once()
        nb.update_tenant.assert_called_once()
        update_args = nb.update_tenant.call_args[0]
        assert update_args[0] == 10
        assert update_args[1]["custom_fields"]["twenty_company_id"] == "comp-new"
        assert update_args[1]["custom_fields"]["twenty_company_url"] == "http://twenty:3000/object/company/comp-new"

    @pytest.mark.anyio
    async def test_tenant_deleted_clears_company_fields(self, engine):
        eng, nb, twenty = engine
        tenant = {
            "id": 10,
            "custom_fields": {"twenty_company_id": "comp-1"},
        }

        await eng._sync_tenant_to_company("deleted", tenant)

        twenty.update_company.assert_called_once()
        args = twenty.update_company.call_args[0]
        assert args[1]["customFields"]["netbox_tenantId"] is None
        assert args[1]["customFields"]["netboxTenantUrl"] is None

    @pytest.mark.anyio
    async def test_tenant_deleted_noop_without_company_id(self, engine):
        eng, nb, twenty = engine
        tenant = {"id": 10, "custom_fields": {}}

        await eng._sync_tenant_to_company("deleted", tenant)

        twenty.update_company.assert_not_called()

    @pytest.mark.anyio
    async def test_unhandled_model_ignored(self, engine):
        eng, nb, twenty = engine
        await eng.handle_netbox_event({"model": "device", "action": "created", "data": {}})
        twenty.update_company.assert_not_called()
        twenty.create_company.assert_not_called()


# ------------------------------------------------------------------
# VRF / Prefix sync tests
# ------------------------------------------------------------------

class TestInfraSync:
    @pytest.mark.anyio
    async def test_vrf_synced_to_netbox_resource(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "Mgmt VRF", "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("vrf", "created", vrf)

        twenty.create_netbox_resource.assert_called_once()
        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["name"] == "Mgmt VRF"
        assert call_args["type"] == "VRF"
        assert call_args["netboxId"] == "5"
        assert call_args["netboxUrl"] == "http://netbox:8000/ipam/vrfs/5/"

    @pytest.mark.anyio
    async def test_prefix_synced_to_netbox_resource(self, engine):
        eng, nb, twenty = engine
        prefix = {"id": 20, "prefix": "10.0.0.0/8", "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("prefix", "created", prefix)

        twenty.create_netbox_resource.assert_called_once()
        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["name"] == "10.0.0.0/8"
        assert call_args["type"] == "Prefix"
        assert call_args["prefixCidr"] == "10.0.0.0/8"
        assert call_args["netboxUrl"] == "http://netbox:8000/ipam/prefixes/20/"

    @pytest.mark.anyio
    async def test_vrf_update_patches_existing_resource(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "Updated VRF", "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = [{"id": "res-1", "name": "Old VRF", "type": "VRF"}]

        await eng._sync_infra_to_netbox_resource("vrf", "updated", vrf)

        twenty.update_netbox_resource.assert_called_once_with("res-1", {
            "name": "Updated VRF",
            "type": "VRF",
            "prefixCidr": "",
            "netboxId": "5",
            "companyId": "10",
            "netboxUrl": "http://netbox:8000/ipam/vrfs/5/",
        })

    @pytest.mark.anyio
    async def test_vrf_update_skips_if_identical(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "Mgmt VRF", "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = [{
            "id": "res-1",
            "name": "Mgmt VRF",
            "type": "VRF",
            "prefixCidr": "",
            "netboxId": "5",
            "netboxUrl": "http://netbox:8000/ipam/vrfs/5/",
        }]

        await eng._sync_infra_to_netbox_resource("vrf", "updated", vrf)

        twenty.update_netbox_resource.assert_not_called()

    @pytest.mark.anyio
    async def test_vrf_delete_removes_resource(self, engine):
        eng, nb, twenty = engine
        twenty.get_netbox_resources.return_value = [{"id": "res-1"}]

        await eng._sync_infra_to_netbox_resource("vrf", "deleted", {"id": 5})

        twenty._delete.assert_called_once_with("/rest/netboxResources/res-1")

    @pytest.mark.anyio
    async def test_vrf_delete_noop_when_no_record(self, engine):
        eng, nb, twenty = engine
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("vrf", "deleted", {"id": 5})

        twenty._delete.assert_not_called()

    @pytest.mark.anyio
    async def test_prefix_falls_back_to_cidr_field(self, engine):
        eng, nb, twenty = engine
        prefix = {"id": 20, "cidr": "192.168.0.0/16", "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("prefix", "created", prefix)

        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["name"] == "192.168.0.0/16"
        assert call_args["prefixCidr"] == "192.168.0.0/16"

    @pytest.mark.anyio
    async def test_resource_falls_back_to_generated_name(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 7, "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("vrf", "created", vrf)

        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["name"] == "VRF-7"

    @pytest.mark.anyio
    async def test_tenant_id_extracted_from_dict(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "VRF", "tenant": {"id": 42}}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("vrf", "created", vrf)

        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["companyId"] == "42"

    @pytest.mark.anyio
    async def test_tenant_id_extracted_from_string(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "VRF", "tenant": 42}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("vrf", "created", vrf)

        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["companyId"] == "42"


# ------------------------------------------------------------------
# Event routing tests
# ------------------------------------------------------------------

class TestEventRouting:
    @pytest.mark.anyio
    async def test_company_created_routes_correctly(self, engine):
        eng, nb, twenty = engine
        nb.create_tenant.return_value = {"id": 1}
        await eng.handle_twenty_event({"event": "company.created", "data": {"id": "c1", "name": "X"}})
        nb.create_tenant.assert_called_once()

    @pytest.mark.anyio
    async def test_company_updated_routes_correctly(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.return_value = {"id": 1, "name": "X", "slug": "x", "custom_fields": {"twenty_company_id": "c1"}}
        await eng.handle_twenty_event({"event": "company.updated", "data": {"id": "c1", "name": "X"}})
        # No update needed (identical) → not called
        nb.update_tenant.assert_not_called()

    @pytest.mark.anyio
    async def test_company_deleted_routes_correctly(self, engine):
        eng, nb, twenty = engine
        await eng.handle_twenty_event({
            "event": "company.deleted",
            "data": {"id": "c1", "customFields": {"netbox_tenantId": "5"}},
        })
        nb.delete_tenant.assert_called_once_with(5)

    @pytest.mark.anyio
    async def test_netbox_tenant_routes_correctly(self, engine):
        eng, nb, twenty = engine
        twenty.get_companies.return_value = [{"id": "c1"}]
        twenty.get_company.return_value = {"id": "c1", "customFields": {}}
        await eng.handle_netbox_event({
            "model": "tenant",
            "action": "created",
            "data": {"id": 1, "name": "T", "slug": "t", "custom_fields": {}},
        })
        twenty.update_company.assert_called_once()

    @pytest.mark.anyio
    async def test_netbox_vrf_routes_correctly(self, engine):
        eng, nb, twenty = engine
        twenty.get_netbox_resources.return_value = []
        await eng.handle_netbox_event({
            "model": "vrf",
            "action": "created",
            "data": {"id": 1, "name": "V", "tenant": {"id": 10}},
        })
        twenty.create_netbox_resource.assert_called_once()
