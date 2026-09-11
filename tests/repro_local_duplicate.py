"""Local end-to-end reproduction of the prod 400 duplicate-person bug.

This script targets the running docker-compose.test.yml stack:
  - NetBox      http://localhost:8100
  - Twenty      http://localhost:3100
  - (middleware stopped during the run to avoid interference)

It seeds the exact prod-inconsistent state:
  * A Twenty Person exists carrying netboxContactId == <netbox contact id>
  * The NetBox contact points back at a STALE twenty_person_id that 404s

Then it runs the sync engine twice:
  * BEFORE  -> simulates the pre-fix behaviour (no netboxContactId GraphQL
               index, no GraphQL fallback): the engine tries to re-create the
               person and Twenty rejects it with HTTP 400 (duplicate), exactly
               like prod.
  * AFTER   -> uses the real (fixed) code: the GraphQL netboxContactId scan
               finds the existing person, repairs the stale linkage, and no
               duplicate is created.

Run with:  uv run python tests/repro_local_duplicate.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import AsyncMock

from dotenv import load_dotenv

from app.config import Settings
from app.provisioning.twenty_bootstrap import _ensure_person_custom_fields
from app.services.netbox_client import NetBoxClient
from app.services.sync_engine import SyncEngine
from app.services.twenty_client import TwentyClient

load_dotenv(".env.test")

# When run from the host (not inside the compose network) the internal service
# hostnames are unreachable; point at the published localhost ports instead.
os.environ["NETBOX_URL"] = "http://localhost:8100"
os.environ["TWENTY_URL"] = "http://localhost:3100"
os.environ["NETBOX_PUBLIC_URL"] = "http://localhost:8100"
os.environ["TWENTY_PUBLIC_URL"] = "http://localhost:3100"
os.environ["VALKEY_HOST"] = "localhost"
os.environ["VALKEY_PORT"] = "6380"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("repro")


async def _cleanup(nb: NetBoxClient, tw: TwentyClient, tag: str) -> None:
    """Remove any test artifacts left by previous runs."""
    try:
        contacts = await nb.get_contacts()
        for c in contacts:
            if (c.get("name") or "").startswith("Repro Dup"):
                await nb.delete_contact(c["id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleanup contacts failed: %s", exc)
    try:
        people = await tw.get_people()
        for p in people:
            email = (p.get("emails") or {}).get("primaryEmail") or ""
            if email.startswith(tag):
                await tw.delete_person(p["id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleanup people failed: %s", exc)


async def main() -> None:
    settings = Settings()
    nb = NetBoxClient(settings)
    tw = TwentyClient(settings)
    await _ensure_person_custom_fields(tw)

    tag = f"repro.dup.{int(asyncio.get_event_loop().time())}"
    email = f"{tag}@example.com"

    await _cleanup(nb, tw, "repro.dup.")

    # --- Seed the inconsistent state -------------------------------------
    contact = await nb.create_contact(
        {
            "name": "Repro Dup Person",
            "email": email,
            "phone": "0704-677005",
        }
    )
    cid = contact["id"]
    logger.info("created NetBox contact id=%s (email %s)", cid, email)

    real_person = await tw.create_person(
        {
            "name": {"firstName": "Repro", "lastName": "Dup Person"},
            "emails": {"primaryEmail": email},
            "phones": {"primaryPhoneNumber": "+46704677005"},
            "netboxContactId": str(cid),
            "netboxContactUrl": {
                "primaryLinkUrl": f"{settings.netbox_public_url}/tenancy/contacts/{cid}"
            },
        }
    )
    real_pid = real_person["id"]
    logger.info("created Twenty person id=%s with netboxContactId=%s", real_pid, cid)

    # A valid-format UUID that simply does not exist in Twenty (returns 404,
    # like the prod stale ids did) -- not an invalid UUID (which 400s).
    stale_id = "0947af77-1a2b-4c3d-8e5f-0abcdef12345"
    await nb.update_contact(cid, {"custom_fields": {"twenty_person_id": stale_id}})
    logger.info("corrupted linkage: contact %s -> stale twenty_person_id=%s", cid, stale_id)

    # --- BEFORE: simulate the pre-fix broken scan -------------------------
    print("\n=== BEFORE (pre-fix: netboxContactId GraphQL index missing) ===")
    broken = SyncEngine(settings)
    broken._twenty.get_people_by_netbox_contact_id = AsyncMock(return_value={})
    broken._twenty.get_person_by_contact_id = AsyncMock(return_value=None)
    refreshed = await nb.get_contact(cid)
    try:
        await broken._sync_contact_to_person("object_updated", refreshed)
        print("[BEFORE] unexpected: no error raised")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        print(f"[BEFORE] reproduced prod bug -> {type(exc).__name__}: {msg[:160]}")
    await broken.close()

    # --- AFTER: real fixed code ------------------------------------------
    print("\n=== AFTER (fixed: real netboxContactId GraphQL index) ===")
    fixed = SyncEngine(settings)
    contact2 = await nb.get_contact(cid)
    try:
        await fixed._sync_contact_to_person("object_updated", contact2)
        print("[AFTER] sync succeeded (no 400)")
    except Exception as exc:  # noqa: BLE001
        print(f"[AFTER] ERROR -> {type(exc).__name__}: {str(exc)[:160]}")
        await fixed.close()
        await nb.close()
        await tw.close()
        return

    # --- Verify ----------------------------------------------------------
    await fixed.reconcile_people()
    await fixed.close()

    refreshed = await nb.get_contact(cid)
    new_pid = (refreshed.get("custom_fields") or {}).get("twenty_person_id")
    idx = await tw.get_people_by_netbox_contact_id()
    same = [p for p in idx.values() if str(p.get("netboxContactId")) == str(cid)]

    print("\n=== VERIFY ===")
    print(f"contact {cid} twenty_person_id now = {new_pid} (expected {real_pid})")
    print(f"persons with netboxContactId={cid}: {len(same)} (expected 1)")

    await nb.close()
    await tw.close()

    ok = new_pid == real_pid and len(same) == 1
    print("\nRESULT:", "FIX VERIFIED LOCALLY" if ok else "FIX NOT VERIFIED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
