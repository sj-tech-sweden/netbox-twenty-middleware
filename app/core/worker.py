from __future__ import annotations

import asyncio
import json
import logging

from app.config import Settings
from app.services.sync_engine import SyncEngine

logger = logging.getLogger("netbox_twenty.worker")

settings = Settings()


async def consume_queue(queue_name: str, handler_name: str) -> None:
    """Poll a Valkey list queue and dispatch events to the sync engine."""
    engine = SyncEngine(settings)

    from valkey import Valkey

    client = Valkey(
        host=settings.valkey_host,
        port=settings.valkey_port,
        decode_responses=True,
    )
    await client.ping()
    logger.info("Worker connected to Valkey, consuming from '%s'", queue_name)

    try:
        while True:
            # Block-pop with a 5-second timeout
            result = await client.blpop(queue_name, timeout=5)
            if result is None:
                continue

            _queue, raw_data = result
            try:
                job = json.loads(raw_data)
                payload = job.get("payload", {})

                if queue_name == "twenty_events":
                    await engine.handle_twenty_event(payload)
                elif queue_name == "netbox_events":
                    await engine.handle_netbox_event(payload)

                logger.info("Processed %s event successfully", handler_name)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in queue %s: %s", queue_name, raw_data[:200])
            except Exception:
                logger.exception("Failed to process %s event", handler_name)
    except asyncio.CancelledError:
        logger.info("Worker shutting down")
    finally:
        await engine.close()
        await client.aclose()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting background worker")

    twenty_task = asyncio.create_task(consume_queue("twenty_events", "twenty"))
    netbox_task = asyncio.create_task(consume_queue("netbox_events", "netbox"))

    await asyncio.gather(twenty_task, netbox_task)


if __name__ == "__main__":
    asyncio.run(main())
