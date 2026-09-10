from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_settings():
    import app.api.v1.webhooks as wh_mod

    with patch.object(wh_mod, "_settings", None), patch("app.api.v1.webhooks.Settings") as mock_cls:
        instance = mock_cls.return_value
        instance.twenty_webhook_token = "test-twenty-token"
        instance.netbox_webhook_secret = "test-netbox-secret"
        instance.valkey_host = "localhost"
        instance.valkey_port = 6379
        yield instance


def _twenty_payload(event: str = "company.created", data: dict[str, Any] | None = None) -> bytes:
    return json.dumps(
        {
            "event": event,
            "data": data or {"id": "comp-123", "name": "Acme Corp"},
        }
    ).encode()


def _netbox_signature(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()


def _netbox_payload(
    model: str = "tenant",
    action: str = "created",
    data: dict[str, Any] | None = None,
) -> bytes:
    return json.dumps(
        {
            "model": model,
            "action": action,
            "data": data
            or {"id": 1, "name": "Acme Corp", "slug": "acme-corp", "custom_fields": {}},
        }
    ).encode()


# ------------------------------------------------------------------
# Twenty webhook tests
# ------------------------------------------------------------------


class TestTwentyWebhook:
    @pytest.mark.anyio
    async def test_requires_auth(self, mock_settings):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/webhooks/twenty", content=b"{}")
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_accepts_valid_bearer(self, mock_settings):
        with patch("app.api.v1.webhooks.get_valkey_client") as mock_vk:
            mock_client = MagicMock()
            mock_client.incr = AsyncMock(return_value=1)
            mock_client.rpush = AsyncMock()
            mock_vk.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/webhooks/twenty",
                    content=_twenty_payload(),
                    headers={"Authorization": "Bearer test-twenty-token"},
                )
            assert resp.status_code == 202

    @pytest.mark.anyio
    async def test_rejects_invalid_token(self, mock_settings):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhooks/twenty",
                content=b"{}",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_accepts_query_token(self, mock_settings):
        with patch("app.api.v1.webhooks.get_valkey_client") as mock_vk:
            mock_client = MagicMock()
            mock_client.incr = AsyncMock(return_value=1)
            mock_client.rpush = AsyncMock()
            mock_vk.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/webhooks/twenty?token=test-twenty-token",
                    content=_twenty_payload(),
                )
            assert resp.status_code == 202

    @pytest.mark.anyio
    async def test_bearer_takes_precedence_over_query(self, mock_settings):
        with patch("app.api.v1.webhooks.get_valkey_client") as mock_vk:
            mock_client = MagicMock()
            mock_client.incr = AsyncMock(return_value=1)
            mock_client.rpush = AsyncMock()
            mock_vk.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/webhooks/twenty?token=wrong",
                    content=_twenty_payload(),
                    headers={"Authorization": "Bearer test-twenty-token"},
                )
            assert resp.status_code == 202

    @pytest.mark.anyio
    async def test_missing_bearer_prefix_rejected(self, mock_settings):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhooks/twenty",
                content=b"{}",
                headers={"Authorization": "test-twenty-token"},
            )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_returns_event_in_response(self, mock_settings):
        with patch("app.api.v1.webhooks.get_valkey_client") as mock_vk:
            mock_client = MagicMock()
            mock_client.incr = AsyncMock(return_value=1)
            mock_client.rpush = AsyncMock()
            mock_vk.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/webhooks/twenty",
                    content=_twenty_payload("company.updated"),
                    headers={"Authorization": "Bearer test-twenty-token"},
                )
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["event"] == "company.updated"


# ------------------------------------------------------------------
# NetBox webhook tests
# ------------------------------------------------------------------


class TestNetboxWebhook:
    @pytest.mark.anyio
    async def test_requires_signature(self, mock_settings):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/webhooks/netbox", content=b"{}")
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_accepts_valid_signature(self, mock_settings):
        body = _netbox_payload()
        sig = _netbox_signature(body, "test-netbox-secret")

        with patch("app.api.v1.webhooks.get_valkey_client") as mock_vk:
            mock_client = MagicMock()
            mock_client.incr = AsyncMock(return_value=1)
            mock_client.rpush = AsyncMock()
            mock_vk.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/webhooks/netbox",
                    content=body,
                    headers={"X-Hook-Signature": sig},
                )
            assert resp.status_code == 202

    @pytest.mark.anyio
    async def test_rejects_invalid_signature(self, mock_settings):
        body = _netbox_payload()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhooks/netbox",
                content=body,
                headers={"X-Hook-Signature": "bad-sig"},
            )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_returns_model_and_action(self, mock_settings):
        body = _netbox_payload("vrf", "updated")
        sig = _netbox_signature(body, "test-netbox-secret")

        with patch("app.api.v1.webhooks.get_valkey_client") as mock_vk:
            mock_client = MagicMock()
            mock_client.incr = AsyncMock(return_value=1)
            mock_client.rpush = AsyncMock()
            mock_vk.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/webhooks/netbox",
                    content=body,
                    headers={"X-Hook-Signature": sig},
                )
            body_resp = resp.json()
            assert body_resp["status"] == "accepted"
            assert body_resp["model"] == "vrf"
            assert body_resp["action"] == "updated"


# ------------------------------------------------------------------
# Health endpoint tests
# ------------------------------------------------------------------


class TestHealthEndpoints:
    @pytest.mark.anyio
    async def test_healthz(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.anyio
    async def test_readyz_returns_json(self):
        with (
            patch("app.api.v1.router.get_valkey_client") as mock_vk,
            patch("app.api.v1.router.NetBoxClient") as mock_nb_cls,
        ):
            mock_client = MagicMock()
            mock_client.ping = AsyncMock()
            mock_vk.return_value = mock_client

            nb_instance = AsyncMock()
            nb_instance.is_healthy.return_value = True
            nb_instance.close = AsyncMock()
            mock_nb_cls.return_value = nb_instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/readyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"]["valkey"] == "ok"
        assert data["checks"]["netbox"] == "ok"
