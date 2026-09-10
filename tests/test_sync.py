from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings
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
        patch("app.services.sync_engine.NetBoxClient") as mock_nb_cls,
        patch("app.services.sync_engine.TwentyClient") as mock_twenty_cls,
    ):
        nb = mock_nb_cls.return_value
        twenty = mock_twenty_cls.return_value

        # NetBox client async methods
        nb.get_tenant = AsyncMock(return_value=None)
        nb.get_tenants = AsyncMock(return_value=[])
        nb.get_contacts = AsyncMock(return_value=[])
        nb.get_contact_by_person_id = AsyncMock(return_value=None)
        nb.get_contact_assignments = AsyncMock(return_value=[])
        nb.create_contact = AsyncMock(return_value={"id": 1})
        nb.update_contact = AsyncMock()
        nb.get_vrfs = AsyncMock(return_value=[])
        nb.get_prefixes = AsyncMock(return_value=[])
        nb.create_tenant = AsyncMock(return_value={"id": 1})
        nb.update_tenant = AsyncMock()
        nb.delete_tenant = AsyncMock()
        nb.get_custom_fields = AsyncMock(return_value=[])
        nb.create_custom_field = AsyncMock()
        nb.get_event_rules = AsyncMock(return_value=[])
        nb.create_event_rule = AsyncMock()
        nb.update_event_rule = AsyncMock()
        nb.is_healthy = AsyncMock(return_value=True)
        nb.close = AsyncMock()

        # Twenty client async methods
        twenty.get_company = AsyncMock(return_value=None)
        twenty.get_companies = AsyncMock(return_value=[])
        twenty.get_people = AsyncMock(return_value=[])
        twenty.get_people_by_netbox_contact_id = AsyncMock(return_value={})
        twenty.create_company = AsyncMock(return_value={"id": "new"})
        twenty.update_company = AsyncMock()
        twenty.get_company_custom_fields = AsyncMock(return_value=[])
        twenty.create_field = AsyncMock()
        twenty.get_object_metadata = AsyncMock(return_value=None)
        twenty.create_object = AsyncMock()
        twenty.create_object_field = AsyncMock()
        twenty.get_webhooks = AsyncMock(return_value=[])
        twenty.create_webhook = AsyncMock()
        twenty.update_webhook = AsyncMock()
        twenty.get_netbox_resources = AsyncMock(return_value=[])
        twenty.create_netbox_resource = AsyncMock(return_value={"id": "res-new"})
        twenty.update_netbox_resource = AsyncMock()
        twenty._delete = AsyncMock()
        twenty._get = AsyncMock()
        twenty._post = AsyncMock()
        twenty._patch = AsyncMock()
        twenty.close = AsyncMock()

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
        assert (
            call_args["custom_fields"]["twenty_company_url"]
            == "http://twenty:3000/object/company/comp-1"
        )

        twenty.update_company.assert_called_once()
        update_args = twenty.update_company.call_args[0]
        assert update_args[0] == "comp-1"
        assert update_args[1]["netboxTenantUrl"] == {
            "primaryLinkUrl": "http://netbox:8000/tenancy/tenants/42/"
        }

    @pytest.mark.anyio
    async def test_company_created_with_existing_tenant_id_updates(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.return_value = {
            "id": 42,
            "name": "Old Name",
            "slug": "old-name",
            "custom_fields": {"twenty_company_id": "comp-1", "twenty_company_url": "old-url"},
        }

        await eng._sync_company_to_tenant(
            {
                "id": "comp-1",
                "name": "New Name",
                "netboxTenantId": "42",
            }
        )

        nb.update_tenant.assert_called_once()
        patch_args = nb.update_tenant.call_args[0]
        assert patch_args[0] == 42
        assert patch_args[1]["name"] == "New Name"
        assert patch_args[1]["slug"] == "new-name"
        assert (
            patch_args[1]["custom_fields"]["twenty_company_url"]
            == "http://twenty:3000/object/company/comp-1"
        )

    @pytest.mark.anyio
    async def test_company_updated_patches_tenant(self, engine):
        eng, nb, twenty = engine
        nb.get_tenant.return_value = {
            "id": 42,
            "name": "Old Name",
            "slug": "old-name",
            "custom_fields": {"twenty_company_id": "comp-1"},
        }

        await eng._sync_company_to_tenant(
            {
                "id": "comp-1",
                "name": "New Name",
                "netboxTenantId": "42",
            }
        )

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

        await eng._sync_company_to_tenant(
            {
                "id": "comp-1",
                "name": "Acme Corp",
            }
        )

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
            "netboxTenantId": "42",
        }
        await eng._delete_tenant_for_company(company)
        nb.delete_tenant.assert_called_once_with(42)

    @pytest.mark.anyio
    async def test_company_deleted_noop_without_tenant_id(self, engine):
        eng, nb, twenty = engine
        await eng._delete_tenant_for_company({"id": "comp-1"})
        nb.delete_tenant.assert_not_called()

    @pytest.mark.anyio
    async def test_company_deleted_handles_netbox_error(self, engine):
        eng, nb, twenty = engine
        nb.delete_tenant.side_effect = Exception("NetBox error")
        # Should not raise
        await eng._delete_tenant_for_company(
            {
                "id": "comp-1",
                "customFields": {"netbox_tenantId": "42"},
            }
        )

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
            "custom_fields": {"twenty_company_id": "comp-1"},
        }
        twenty.get_companies.return_value = [{"id": "comp-1"}]
        twenty.get_company.return_value = {"id": "comp-1"}

        await eng._sync_tenant_to_company("created", tenant)

        twenty.update_company.assert_called_once()
        args = twenty.update_company.call_args[0]
        assert args[0] == "comp-1"
        assert args[1]["netboxTenantId"] == "10"
        assert args[1]["netboxTenantUrl"] == {
            "primaryLinkUrl": "http://netbox:8000/tenancy/tenants/10/"
        }

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
            "netboxTenantId": "10",
            "netboxTenantSlug": "acme-corp",
            "netboxTenantUrl": {"primaryLinkUrl": "http://netbox:8000/tenancy/tenants/10/"},
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
            "netboxTenantId": "10",
            "netboxTenantUrl": "old-url",
        }

        await eng._sync_tenant_to_company("created", tenant)

        twenty.update_company.assert_called_once()
        args = twenty.update_company.call_args[0]
        assert args[1]["netboxTenantUrl"] == {
            "primaryLinkUrl": "http://netbox:8000/tenancy/tenants/10/"
        }

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
        assert (
            update_args[1]["custom_fields"]["twenty_company_url"]
            == "http://twenty:3000/object/company/comp-new"
        )

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
        assert args[1]["netboxTenantId"] is None
        assert args[1]["netboxTenantUrl"] == {"primaryLinkUrl": ""}

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
        assert call_args["resourcetype"] == "VRF"
        assert call_args["netboxid"] == "5"
        assert call_args["netboxurl"] == {"primaryLinkUrl": "http://netbox:8000/ipam/vrfs/5/"}

    @pytest.mark.anyio
    async def test_prefix_synced_to_netbox_resource(self, engine):
        eng, nb, twenty = engine
        prefix = {"id": 20, "prefix": "10.0.0.0/8", "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("prefix", "created", prefix)

        twenty.create_netbox_resource.assert_called_once()
        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["name"] == "10.0.0.0/8"
        assert call_args["resourcetype"] == "PREFIX"
        assert call_args["prefixcidr"] == "10.0.0.0/8"
        assert call_args["netboxurl"] == {"primaryLinkUrl": "http://netbox:8000/ipam/prefixes/20/"}

    @pytest.mark.anyio
    async def test_vrf_update_patches_existing_resource(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "Updated VRF", "tenant": {"id": 10}}
        existing = [
            {
                "id": "res-1",
                "name": "Old VRF",
                "resourcetype": "VRF",
                "netboxid": "5",
            }
        ]
        twenty.get_netbox_resources.return_value = existing

        await eng._sync_infra_to_netbox_resource("vrf", "updated", vrf)

        twenty.update_netbox_resource.assert_called_once_with(
            "res-1",
            {
                "name": "Updated VRF",
                "resourcetype": "VRF",
                "prefixcidr": "",
                "netboxid": "5",
                "companyid": "10",
                "netboxurl": {"primaryLinkUrl": "http://netbox:8000/ipam/vrfs/5/"},
            },
        )

    @pytest.mark.anyio
    async def test_vrf_update_skips_if_identical(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "Mgmt VRF", "tenant": {"id": 10}}
        twenty.get_netbox_resources.return_value = [
            {
                "id": "res-1",
                "name": "Mgmt VRF",
                "resourcetype": "VRF",
                "prefixcidr": "",
                "netboxid": "5",
                "netboxurl": {"primaryLinkUrl": "http://netbox:8000/ipam/vrfs/5/"},
            }
        ]

        await eng._sync_infra_to_netbox_resource("vrf", "updated", vrf)

        twenty.update_netbox_resource.assert_not_called()

    @pytest.mark.anyio
    async def test_vrf_delete_removes_resource(self, engine):
        eng, nb, twenty = engine
        twenty.get_netbox_resources.return_value = [
            {"id": "res-1", "resourcetype": "VRF", "netboxid": "5"}
        ]

        await eng._sync_infra_to_netbox_resource("vrf", "deleted", {"id": 5})

        twenty._delete.assert_called_once_with("/rest/netboxresources/res-1")

    @pytest.mark.anyio
    async def test_prefix_and_vrf_same_netboxid_do_not_collide(self, engine):
        eng, nb, twenty = engine
        # A VRF with netboxid "2" already exists; a Prefix with the same netboxid
        # must NOT be matched to it (different resourcetype).
        twenty.get_netbox_resources.return_value = [
            {
                "id": "res-vrf",
                "name": "VRF 2",
                "resourcetype": "VRF",
                "netboxid": "2",
            }
        ]
        prefix = {"id": 2, "prefix": "10.0.0.0/8", "tenant": {"id": 10}}

        await eng._sync_infra_to_netbox_resource("prefix", "created", prefix)

        twenty.create_netbox_resource.assert_called_once()
        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["resourcetype"] == "PREFIX"
        assert call_args["netboxid"] == "2"
        twenty.update_netbox_resource.assert_not_called()

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
        assert call_args["prefixcidr"] == "192.168.0.0/16"

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
        assert call_args["companyid"] == "42"

    @pytest.mark.anyio
    async def test_tenant_id_extracted_from_string(self, engine):
        eng, nb, twenty = engine
        vrf = {"id": 5, "name": "VRF", "tenant": 42}
        twenty.get_netbox_resources.return_value = []

        await eng._sync_infra_to_netbox_resource("vrf", "created", vrf)

        call_args = twenty.create_netbox_resource.call_args[0][0]
        assert call_args["companyid"] == "42"


# ------------------------------------------------------------------
# Event routing tests
# ------------------------------------------------------------------


class TestEventRouting:
    @pytest.mark.anyio
    async def test_company_created_routes_correctly(self, engine):
        eng, nb, twenty = engine
        nb.create_tenant.return_value = {"id": 1}
        event = {"event": "company.created", "data": {"id": "c1", "name": "X"}}
        await eng.handle_twenty_event(event)
        nb.create_tenant.assert_called_once()

    @pytest.mark.anyio
    async def test_company_updated_routes_correctly(self, engine):
        eng, nb, twenty = engine
        tenant_data = {
            "id": 1,
            "name": "X",
            "slug": "x",
            "custom_fields": {"twenty_company_id": "c1"},
        }
        nb.get_tenant.return_value = tenant_data
        event = {"event": "company.updated", "data": {"id": "c1", "name": "X"}}
        await eng.handle_twenty_event(event)
        # No update needed (identical) → not called
        nb.update_tenant.assert_not_called()

    @pytest.mark.anyio
    async def test_company_deleted_routes_correctly(self, engine):
        eng, nb, twenty = engine
        await eng.handle_twenty_event(
            {
                "event": "company.deleted",
                "data": {"id": "c1", "netboxTenantId": "5"},
            }
        )
        nb.delete_tenant.assert_called_once_with(5)

    @pytest.mark.anyio
    async def test_netbox_tenant_routes_correctly(self, engine):
        eng, nb, twenty = engine
        twenty.get_companies.return_value = [{"id": "c1"}]
        twenty.get_company.return_value = {"id": "c1", "customFields": {}}
        await eng.handle_netbox_event(
            {
                "model": "tenant",
                "action": "created",
                "data": {"id": 1, "name": "T", "slug": "t", "custom_fields": {}},
            }
        )
        twenty.create_company.assert_called_once()

    @pytest.mark.anyio
    async def test_netbox_vrf_routes_correctly(self, engine):
        eng, nb, twenty = engine
        twenty.get_netbox_resources.return_value = []
        await eng.handle_netbox_event(
            {
                "model": "vrf",
                "action": "created",
                "data": {"id": 1, "name": "V", "tenant": {"id": 10}},
            }
        )
        twenty.create_netbox_resource.assert_called_once()


class TestReconcile:
    @pytest.mark.anyio
    async def test_reconcile_all_empty(self, engine):
        eng, nb, twenty = engine
        await eng.reconcile_all()
        nb.create_tenant.assert_not_called()
        twenty.create_company.assert_not_called()

    @pytest.mark.anyio
    async def test_reconcile_companies_links_existing(self, engine):
        eng, nb, twenty = engine
        tenant = {
            "id": 10,
            "name": "T",
            "slug": "t",
            "custom_fields": {"twenty_company_id": "c1"},
        }
        company = {
            "id": "c1",
            "name": "T",
            "netboxTenantId": "10",
            "netboxTenantSlug": "t",
        }
        nb.get_tenants.return_value = [tenant]
        twenty.get_companies.return_value = [company]
        twenty.get_company.return_value = company
        await eng.reconcile_companies()
        # Found matching tenant already linked; idempotent
        nb.create_tenant.assert_not_called()
        twenty.create_company.assert_not_called()

    @pytest.mark.anyio
    async def test_reconcile_companies_creates_for_unlinked_tenant(self, engine):
        eng, nb, twenty = engine
        nb.get_tenants.return_value = [{"id": 30, "name": "Z", "slug": "z", "custom_fields": {}}]
        twenty.get_companies.return_value = []
        twenty.get_company.return_value = None
        await eng.reconcile_companies()
        twenty.create_company.assert_awaited()

    @pytest.mark.anyio
    async def test_reconcile_people_creates_for_unlinked_person(self, engine):
        eng, nb, twenty = engine
        twenty.get_people.return_value = [
            {
                "id": "p1",
                "name": {"firstName": "A", "lastName": "B"},
                "emails": {"primaryEmail": "a@b.com"},
                "phones": {"primaryPhoneNumber": "+460000"},
                "netboxContactId": "",
                "netboxContactUrl": "",
                "linkedinUrl": "",
                "linkedinPhantomUrl": "",
            }
        ]
        nb.get_contacts.return_value = []
        await eng.reconcile_people()
        nb.create_contact.assert_awaited()

    @pytest.mark.anyio
    async def test_reconcile_resources_creates_for_vrf(self, engine):
        eng, nb, twenty = engine
        nb.get_vrfs.return_value = [{"id": 5, "name": "V", "tenant": {"id": 10}, "rd": "65000:1"}]
        nb.get_prefixes.return_value = []
        twenty.get_netbox_resources.return_value = []
        await eng.reconcile_resources()
        twenty.create_netbox_resource.assert_awaited()

    @pytest.mark.anyio
    async def test_reconcile_resources_creates_for_prefix(self, engine):
        eng, nb, twenty = engine
        nb.get_vrfs.return_value = []
        nb.get_prefixes.return_value = [
            {
                "id": 7,
                "prefix": "10.0.0.0/24",
                "status": {"value": "active"},
                "tenant": {"id": 10},
                "vrf": None,
                "description": "",
                "custom_fields": {},
            }
        ]
        twenty.get_netbox_resources.return_value = []
        await eng.reconcile_resources()
        twenty.create_netbox_resource.assert_awaited()


class TestPeopleDuplicateRepro:
    """Reproduce the prod 400 duplicate-person constraint failures.

    Prod symptom: a NetBox contact carries a stale ``twenty_person_id`` that now
    404s, while the person still exists in Twenty with the same
    ``netboxContactId``. The Twenty REST ``/rest/people`` list omits the
    ``netboxContactId`` custom field, so a match must come from the GraphQL scan.
    Without that match the engine re-created the person and hit the unique
    ``person.IDX_UNIQUE_...`` (netboxContactId) constraint -> HTTP 400.
    """

    @staticmethod
    def _http_400_duplicate() -> httpx.HTTPStatusError:
        req = httpx.Request("POST", "http://twenty:3000/rest/people")
        resp = httpx.Response(
            400,
            request=req,
            text="duplicate entry was detected: unique constraint "
            "person.IDX_UNIQUE_87914cd3ce963115f8cb943e2ac was violated",
        )
        return httpx.HTTPStatusError("400", request=req, response=resp)

    @pytest.mark.anyio
    async def test_reconcile_reuses_existing_person_via_netbox_index(self, engine):
        """Stored id is stale/404, REST omits netboxContactId, GraphQL finds it."""
        eng, nb, twenty = engine
        twenty.get_person = AsyncMock(return_value=None)
        twenty.get_person_by_contact_id = AsyncMock(return_value=None)
        twenty.create_person = AsyncMock(return_value={"id": "new"})
        twenty.update_person = AsyncMock()

        contact = {
            "id": 15,
            "name": "Foo Bar",
            "email": "foo@sj-tech.se",
            "phone": "0704-677005",
            "custom_fields": {"twenty_person_id": "0947af77-stale-404-uuid"},
        }
        # REST list omits the netboxContactId custom field.
        twenty.get_people.return_value = [
            {
                "id": "real-id",
                "name": {"firstName": "Foo", "lastName": "Bar"},
                "emails": {"primaryEmail": "foo@sj-tech.se"},
                "phones": {"primaryPhoneNumber": ""},
            }
        ]
        # GraphQL scan is the authoritative netboxContactId -> person map.
        twenty.get_people_by_netbox_contact_id.return_value = {
            "15": {
                "id": "real-id",
                "netboxContactId": "15",
                "name": {"firstName": "Foo", "lastName": "Bar"},
                "emails": {"primaryEmail": "foo@sj-tech.se"},
            }
        }
        twenty.get_person.return_value = None  # stale stored id 404s
        nb.get_contacts.return_value = [contact]

        await eng.reconcile_people()

        twenty.create_person.assert_not_awaited()
        twenty.update_person.assert_awaited_once()
        assert twenty.update_person.await_args.args[0] == "real-id"
        # Linkage is repaired from the stale id to the real person id.
        nb.update_contact.assert_awaited()
        cf = nb.update_contact.await_args.args[1]["custom_fields"]
        assert cf["twenty_person_id"] == "real-id"

    @pytest.mark.anyio
    async def test_webhook_path_reuses_existing_person_via_graphql(self, engine):
        """Webhook (no pre-fetched index): GraphQL lookup must prevent recreate."""
        eng, nb, twenty = engine
        twenty.get_person = AsyncMock(return_value=None)
        twenty.get_person_by_contact_id = AsyncMock(return_value=None)
        twenty.create_person = AsyncMock(return_value={"id": "new"})
        twenty.update_person = AsyncMock()

        contact = {
            "id": 15,
            "name": "Foo Bar",
            "email": "foo@sj-tech.se",
            "phone": "",
            "custom_fields": {"twenty_person_id": "0947af77-stale-404-uuid"},
        }
        twenty.get_person.return_value = None  # stale stored id 404s
        twenty.get_person_by_contact_id.return_value = {
            "id": "real-id",
            "netboxContactId": "15",
            "name": {"firstName": "Foo", "lastName": "Bar"},
            "emails": {"primaryEmail": "foo@sj-tech.se"},
        }

        await eng._sync_contact_to_person("object_updated", contact)

        twenty.create_person.assert_not_awaited()
        twenty.update_person.assert_awaited_once()
        assert twenty.update_person.await_args.args[0] == "real-id"
        nb.update_contact.assert_awaited()
        cf = nb.update_contact.await_args.args[1]["custom_fields"]
        assert cf["twenty_person_id"] == "real-id"

    @pytest.mark.anyio
    async def test_create_duplicate_400_self_heals_to_existing_person(self, engine):
        """If create still 400s as duplicate, reuse the existing person."""
        eng, nb, twenty = engine
        twenty.get_person = AsyncMock(return_value=None)
        twenty.get_person_by_contact_id = AsyncMock(return_value=None)
        twenty.create_person = AsyncMock(return_value={"id": "new"})
        twenty.update_person = AsyncMock()

        contact = {
            "id": 15,
            "name": "Foo Bar",
            "email": "foo@sj-tech.se",
            "phone": "",
            "custom_fields": {"twenty_person_id": ""},
        }
        twenty.get_person.return_value = None
        twenty.create_person = AsyncMock(side_effect=self._http_400_duplicate())
        # GraphQL scan misses on the initial match attempt, but the self-heal
        # lookup (triggered by the duplicate 400) finds the existing person.
        twenty.get_person_by_contact_id = AsyncMock(
            side_effect=[
                None,
                {
                    "id": "real-id",
                    "netboxContactId": "15",
                    "name": {"firstName": "Foo", "lastName": "Bar"},
                    "emails": {"primaryEmail": "foo@sj-tech.se"},
                },
            ]
        )

        await eng._sync_contact_to_person("object_updated", contact, people=[], people_by_netbox={})

        twenty.create_person.assert_awaited()
        twenty.update_person.assert_awaited_once()
        assert twenty.update_person.await_args.args[0] == "real-id"
        nb.update_contact.assert_awaited()
        cf = nb.update_contact.await_args.args[1]["custom_fields"]
        assert cf["twenty_person_id"] == "real-id"


class TestPhoneNormalization:
    @pytest.mark.anyio
    async def test_normalize_phone_strips_formatting_and_adds_cc(self, engine):
        eng, nb, twenty = engine
        assert eng._normalize_phone("0704-677005") == "+46704677005"
        assert eng._normalize_phone("767692700") == "+46767692700"

    @pytest.mark.anyio
    async def test_normalize_phone_keeps_existing_e164(self, engine):
        eng, nb, twenty = engine
        assert eng._normalize_phone("+46767692700") == "+46767692700"

    @pytest.mark.anyio
    async def test_normalize_phone_returns_none_for_empty(self, engine):
        eng, nb, twenty = engine
        assert eng._normalize_phone("") is None
        assert eng._normalize_phone(None) is None
