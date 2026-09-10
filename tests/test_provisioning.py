from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings


def _settings():
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


def _client(overrides=None):
    client = MagicMock()
    for method in [
        "get_custom_fields",
        "create_custom_field",
        "get_webhooks",
        "create_webhook",
        "update_webhook",
        "get_event_rules",
        "create_event_rule",
        "update_event_rule",
        "get_company_object_id",
        "get_company_custom_fields",
        "get_object_metadata",
        "create_object",
        "create_field",
        "get_netbox_resources",
        "create_netbox_resource",
        "update_netbox_resource",
        "close",
    ]:
        setattr(client, method, AsyncMock())
    if overrides:
        for k, v in overrides.items():
            getattr(client, k).return_value = v
    return client


class TestNetBoxBootstrap:
    @pytest.mark.anyio
    async def test_creates_missing_custom_fields_and_event_rule(self):
        nb = _client(
            {
                "get_custom_fields": [],
                "get_webhooks": [],
                "get_event_rules": [],
                "create_webhook": {"id": "wh"},
                "create_event_rule": {"id": "er"},
            }
        )
        with patch("app.provisioning.netbox_bootstrap.NetBoxClient", return_value=nb):
            from app.provisioning import netbox_bootstrap

            await netbox_bootstrap.bootstrap_netbox(_settings())

        # 2 tenant fields + 2 contact fields
        assert nb.create_custom_field.await_count == 4
        nb.create_webhook.assert_awaited_once()
        nb.create_event_rule.assert_awaited_once()

    @pytest.mark.anyio
    async def test_skips_existing_custom_fields(self):
        nb = _client(
            {
                "get_custom_fields": [
                    {"name": "twenty_company_id"},
                    {"name": "twenty_company_url"},
                    {"name": "twenty_person_id"},
                    {"name": "twenty_person_url"},
                ],
                "get_webhooks": [
                    {
                        "id": "wh",
                        "payload_url": "http://x/api/v1/webhooks/netbox",
                        "secret": "netbox-secret",
                    }
                ],
                "get_event_rules": [
                    {
                        "id": "er",
                        "action_object_id": "wh",
                        "event_types": [
                            "object_created",
                            "object_updated",
                            "object_deleted",
                        ],
                        "object_types": ["tenancy.tenant", "tenancy.contact"],
                    }
                ],
            }
        )
        with patch("app.provisioning.netbox_bootstrap.NetBoxClient", return_value=nb):
            from app.provisioning import netbox_bootstrap

            await netbox_bootstrap.bootstrap_netbox(_settings())

        nb.create_custom_field.assert_not_called()
        nb.create_webhook.assert_not_called()
        nb.create_event_rule.assert_not_called()


class TestTwentyBootstrap:
    @pytest.mark.anyio
    async def test_creates_custom_fields_object_and_webhook(self):
        t = _client(
            {
                "get_company_object_id": "company-obj-id",
                "get_company_custom_fields": [],
                "get_webhooks": [],
                "create_webhook": {"id": "wh"},
            }
        )

        def _meta(name):
            if name == "netboxresource":
                return None
            return {"id": f"obj-{name}", "fields": []}

        t.get_object_metadata.side_effect = _meta

        with patch("app.provisioning.twenty_bootstrap.TwentyClient", return_value=t):
            from app.provisioning import twenty_bootstrap

            await twenty_bootstrap.bootstrap_twenty(_settings())

        # 3 company fields + 2 person fields + 6 netboxresource fields
        assert t.create_field.await_count == 11
        t.create_object.assert_awaited_once()
        t.create_webhook.assert_awaited_once()

    @pytest.mark.anyio
    async def test_skips_company_person_fields_when_objects_missing(self):
        # Company object id missing and all metadata lookups fail: only the
        # company/person custom fields are skipped; netboxresource object +
        # its 6 fields + webhook provisioning still run.
        t = _client(
            {
                "get_company_object_id": None,
                "get_object_metadata": None,
                "get_webhooks": [],
                "create_webhook": {"id": "wh"},
            }
        )
        with patch("app.provisioning.twenty_bootstrap.TwentyClient", return_value=t):
            from app.provisioning import twenty_bootstrap

            await twenty_bootstrap.bootstrap_twenty(_settings())
        # Only the 6 netboxresource fields are created (company/person skipped)
        assert t.create_field.await_count == 6
        t.create_object.assert_awaited_once()
        t.create_webhook.assert_awaited_once()

    @pytest.mark.anyio
    async def test_existing_netbox_resource_object_not_recreated(self):
        existing_fields = [
            {"name": "name"},
            {"name": "resourcetype"},
            {"name": "prefixcidr"},
            {"name": "netboxid"},
            {"name": "companyid"},
            {"name": "netboxurl"},
        ]

        def _meta(name):
            if name == "netboxresource":
                return {"id": "existing", "fields": existing_fields}
            if name == "person":
                return {
                    "id": "person-obj",
                    "fields": [
                        {"name": "netboxContactId"},
                        {"name": "netboxContactUrl"},
                    ],
                }
            return {"id": "company-obj", "fields": [{"name": "netboxTenantId"}]}

        t = _client(
            {
                "get_company_object_id": "company-obj",
                "get_company_custom_fields": [
                    {"name": "netboxTenantId"},
                    {"name": "netboxTenantSlug"},
                    {"name": "netboxTenantUrl"},
                ],
                "get_webhooks": [
                    {
                        "id": "wh",
                        "targetUrl": "http://localhost:8000/api/v1/webhooks/twenty",
                        "secret": "twenty-token",
                    }
                ],
            }
        )
        t.get_object_metadata.side_effect = _meta

        with patch("app.provisioning.twenty_bootstrap.TwentyClient", return_value=t):
            from app.provisioning import twenty_bootstrap

            await twenty_bootstrap.bootstrap_twenty(_settings())

        t.create_object.assert_not_called()
        t.create_field.assert_not_called()
        t.create_webhook.assert_not_called()
