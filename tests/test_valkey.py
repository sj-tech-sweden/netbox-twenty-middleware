from __future__ import annotations

import json
from unittest.mock import AsyncMock

from app.core.valkey import enqueue_event


class TestEnqueueEvent:
    async def test_enqueues_payload(self):
        client = AsyncMock()
        client.incr.return_value = 1

        key = await enqueue_event(client, "test_queue", {"event": "test"})

        assert key == "job:test_queue:1"
        client.incr.assert_called_once_with("counter:test_queue")
        client.rpush.assert_called_once()

        call_args = client.rpush.call_args[0]
        assert call_args[0] == "test_queue"
        data = json.loads(call_args[1])
        assert data["key"] == "job:test_queue:1"
        assert data["payload"] == {"event": "test"}

    async def test_increments_counter(self):
        client = AsyncMock()
        client.incr.return_value = 5

        key = await enqueue_event(client, "my_queue", {"id": 1})

        assert key == "job:my_queue:5"

    async def test_returns_job_key(self):
        client = AsyncMock()
        client.incr.return_value = 42

        key = await enqueue_event(client, "q", {})

        assert key.startswith("job:q:")
