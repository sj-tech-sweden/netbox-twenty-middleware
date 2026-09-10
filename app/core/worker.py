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

    from valkey.asyncio import Valkey

    client = Valkey(
        host=settings.valkey_host,
        port=settings.valkey_port,
        decode_responses=True,
        socket_timeout=15,
        socket_connect_timeout=5,
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


async def reconcile_loop(engine: SyncEngine, interval: int) -> None:
    """Run an initial full reconciliation, then repeat on a fixed interval.

    This provides the initial sync and catches any changes missed while the
    middleware was offline (webhooks that failed to deliver).
    """
    if interval <= 0:
        return
    try:
        logger.info("Running initial full reconciliation")
        await engine.reconcile_all()
        logger.info("Initial reconciliation complete")
    except Exception:
        logger.exception("Initial reconciliation failed")

    while True:
        await asyncio.sleep(interval)
        try:
            logger.info("Running periodic reconciliation")
            await engine.reconcile_all()
            logger.info("Periodic reconciliation complete")
        except Exception:
            logger.exception("Periodic reconciliation failed")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting background worker")

    twenty_task = asyncio.create_task(consume_queue("twenty_events", "twenty"))
    netbox_task = asyncio.create_task(consume_queue("netbox_events", "netbox"))

    reconcile = asyncio.create_task(
        reconcile_loop(SyncEngine(settings), settings.sync_interval_seconds)
    )

    await asyncio.gather(twenty_task, netbox_task, reconcile)


if __name__ == "__main__":
    asyncio.run(main())
