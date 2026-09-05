from __future__ import annotations

import json
import logging
from typing import Any

import saq
from valkey import Valkey

logger = logging.getLogger("netbox_twenty.valkey")

_client: Valkey | None = None


async def get_valkey_client(host: str = "valkey", port: int = 6379) -> Valkey:
    global _client
    if _client is None:
        _client = Valkey(host=host, port=port, decode_responses=True)
        await _client.ping()
        logger.info("Connected to Valkey at %s:%d", host, port)
    return _client


async def close_valkey() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Valkey connection closed")


def enqueue_event(client: Valkey, queue_name: str, payload: dict[str, Any]) -> str:
    """Push a serialised event onto the given Valkey list queue.

    Returns the job key stored in the queue.
    """
    job_key = f"job:{queue_name}:{client.incr(f'counter:{queue_name}')}"
    data = json.dumps({"key": job_key, "payload": payload})
    client.rpush(queue_name, data)
    logger.info("Enqueued event to %s (key=%s)", queue_name, job_key)
    return job_key


# ---------------------------------------------------------------------------
# saq-based background queue (used by the worker process)
# ---------------------------------------------------------------------------

TASK_REGISTRY: dict[str, saq.Task] = {}


def register_task(name: str, func: Any) -> saq.Task:
    task = saq.Task(func, name=name)
    TASK_REGISTRY[name] = task
    return task


def create_queue(host: str = "valkey", port: int = 6379, queue_name: str = "default") -> saq.Queue:
    valkey_url = f"valkey://{host}:{port}"
    return saq.Queue(name=queue_name, url=valkey_url, tasks=list(TASK_REGISTRY.values()))
