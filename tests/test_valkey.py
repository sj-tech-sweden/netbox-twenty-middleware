from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.core.valkey import enqueue_event


class TestEnqueueEvent:
    def test_enqueues_payload(self):
        client = MagicMock()
        client.incr.return_value = 1

        key = enqueue_event(client, "test_queue", {"event": "test"})

        assert key == "job:test_queue:1"
        client.incr.assert_called_once_with("counter:test_queue")
        client.rpush.assert_called_once()

        call_args = client.rpush.call_args[0]
        assert call_args[0] == "test_queue"
        data = json.loads(call_args[1])
        assert data["key"] == "job:test_queue:1"
        assert data["payload"] == {"event": "test"}

    def test_increments_counter(self):
        client = MagicMock()
        client.incr.return_value = 5

        key = enqueue_event(client, "my_queue", {"id": 1})

        assert key == "job:my_queue:5"

    def test_returns_job_key(self):
        client = MagicMock()
        client.incr.return_value = 42

        key = enqueue_event(client, "q", {})

        assert key.startswith("job:q:")
