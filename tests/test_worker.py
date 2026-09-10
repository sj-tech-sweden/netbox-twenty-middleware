from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.core import worker


@pytest.fixture
def settings():
    return Settings(
        netbox_url="http://netbox:8000",
        netbox_token="nbt_test.key",
        twenty_url="http://twenty:3000",
        twenty_api_key="test-api-key",
        valkey_host="localhost",
        valkey_port=6379,
        public_base_url="http://localhost:8000",
        sync_interval_seconds=300,
    )


def _fake_valkey(blpop_effect):
    fake = MagicMock()
    fake.ping = AsyncMock()
    fake.blpop = AsyncMock(side_effect=blpop_effect)
    fake.aclose = AsyncMock()
    return fake


@pytest.mark.anyio
async def test_consume_queue_twenty_event(settings):
    engine = AsyncMock()
    raw = json.dumps({"payload": {"event": "company.created", "data": {"id": "c1"}}})
    fake = _fake_valkey([("twenty_events", raw), asyncio.CancelledError()])
    with (
        patch("app.core.worker.SyncEngine", return_value=engine),
        patch("valkey.asyncio.Valkey", return_value=fake),
    ):
        await worker.consume_queue("twenty_events", "twenty")
    engine.handle_twenty_event.assert_awaited_once()
    fake.aclose.assert_awaited()


@pytest.mark.anyio
async def test_consume_queue_netbox_event(settings):
    engine = AsyncMock()
    raw = json.dumps({"payload": {"model": "tenant", "action": "created", "data": {}}})
    fake = _fake_valkey([("netbox_events", raw), asyncio.CancelledError()])
    with (
        patch("app.core.worker.SyncEngine", return_value=engine),
        patch("valkey.asyncio.Valkey", return_value=fake),
    ):
        await worker.consume_queue("netbox_events", "netbox")
    engine.handle_netbox_event.assert_awaited_once()


@pytest.mark.anyio
async def test_consume_queue_invalid_json_is_skipped(settings):
    engine = AsyncMock()
    fake = _fake_valkey([("twenty_events", "not-json"), asyncio.CancelledError()])
    with (
        patch("app.core.worker.SyncEngine", return_value=engine),
        patch("valkey.asyncio.Valkey", return_value=fake),
    ):
        await worker.consume_queue("twenty_events", "twenty")
    engine.handle_twenty_event.assert_not_called()


@pytest.mark.anyio
async def test_consume_queue_handler_exception_is_swallowed(settings):
    engine = AsyncMock()
    engine.handle_twenty_event.side_effect = RuntimeError("boom")
    raw = json.dumps({"payload": {"event": "company.created", "data": {}}})
    fake = _fake_valkey([("twenty_events", raw), asyncio.CancelledError()])
    with (
        patch("app.core.worker.SyncEngine", return_value=engine),
        patch("valkey.asyncio.Valkey", return_value=fake),
    ):
        await worker.consume_queue("twenty_events", "twenty")
    engine.handle_twenty_event.assert_awaited_once()


@pytest.mark.anyio
async def test_reconcile_loop_runs_initial_and_stops(settings):
    engine = AsyncMock()

    async def _sleep(_):
        raise asyncio.CancelledError()

    with (
        patch("app.core.worker.SyncEngine", return_value=engine),
        patch("asyncio.sleep", side_effect=_sleep),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker.reconcile_loop(engine, 300)
    engine.reconcile_all.assert_awaited()


@pytest.mark.anyio
async def test_reconcile_loop_skips_when_interval_zero(settings):
    engine = AsyncMock()
    await worker.reconcile_loop(engine, 0)
    engine.reconcile_all.assert_not_called()
