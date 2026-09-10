from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.core.utils import slugify
from app.services.netbox_client import NetBoxClient
from app.services.twenty_client import TwentyClient

logger = logging.getLogger("netbox_twenty.sync_engine")


class SyncEngine:
    """Processes webhook events and synchronises between NetBox and Twenty."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._netbox = NetBoxClient(settings)
        self._twenty = TwentyClient(settings)

    async def close(self) -> None:
        await self._netbox.close()
        await self._twenty.close()

    # ------------------------------------------------------------------
    # Twenty field helpers
    # In this Twenty version custom fields on both standard and custom
    # objects are exposed as top-level fields (not nested under a
    # "customFields" key). LINKS-type URL fields expect an object of the
    # shape {"primaryLinkUrl": "..."}.
    # ------------------------------------------------------------------

    @staticmethod
    def _links(url: str | None) -> dict[str, str]:
        return {"primaryLinkUrl": url or ""}

    @staticmethod
    def _links_url(value: object) -> str:
        if isinstance(value, dict):
            return value.get("primaryLinkUrl") or ""
        return value or "" if isinstance(value, str) else ""

    # ------------------------------------------------------------------
    # Twenty → NetBox
    # ------------------------------------------------------------------

    async def handle_twenty_event(self, payload: dict[str, Any]) -> None:
        # Twenty's webhook envelope uses "eventName" and nests the record
        # under "record". Older/alternative shapes use "event"/"data"/"payload".
        event_type = payload.get("eventName") or payload.get("event", "")
        data = payload.get("record") or payload.get("data") or payload.get("payload") or payload

        match event_type:
            case "company.created" | "company.updated":
                await self._sync_company_to_tenant(data)
            case "company.deleted":
                await self._delete_tenant_for_company(data)
            case "person.created" | "person.updated":
                await self._sync_person_to_contact(data)
            case "person.deleted":
                await self._delete_contact_for_person(data)
            case _:
                logger.debug("Ignoring unhandled Twenty event: %s", event_type)

    def _twenty_company_url(self, company_id: str) -> str:
        base = (self._settings.twenty_public_url or self._settings.twenty_url).rstrip("/")
        return f"{base}/object/company/{company_id}"

    def _netbox_tenant_url(self, tenant_id: str) -> str:
        base = (self._settings.netbox_public_url or self._settings.netbox_url).rstrip("/")
        return f"{base}/tenancy/tenants/{tenant_id}/"

    def _twenty_person_url(self, person_id: str) -> str:
        base = (self._settings.twenty_public_url or self._settings.twenty_url).rstrip("/")
        return f"{base}/object/person/{person_id}"

    def _netbox_contact_url(self, contact_id: str) -> str:
        base = (self._settings.netbox_public_url or self._settings.netbox_url).rstrip("/")
        return f"{base}/tenancy/contacts/{contact_id}/"

    async def _sync_company_to_tenant(self, company: dict[str, Any]) -> None:
        company_id = company.get("id")
        company_name = company.get("name", "Unnamed")
        slug = slugify(company_name)
        # Custom fields are top-level on the company object in this Twenty version.
        existing_tenant_id = company.get("netboxTenantId")
        company_url = self._twenty_company_url(company_id)

        # If we already have a NetBox tenant ID on the company, try to update it
        if existing_tenant_id:
            try:
                tenant = await self._netbox.get_tenant(int(existing_tenant_id))
            except Exception:
                logger.warning(
                    "Stale netboxTenantId %s on company %s",
                    existing_tenant_id,
                    company_id,
                )
                tenant = None

            if tenant:
                # Idempotency: check if update is needed
                update_payload: dict[str, Any] = {}
                if tenant.get("name") != company_name:
                    update_payload["name"] = company_name
                if tenant.get("slug") != slug:
                    update_payload["slug"] = slug

                cf = tenant.get("custom_fields", {})
                if cf.get("twenty_company_id") != company_id:
                    update_payload.setdefault("custom_fields", {})["twenty_company_id"] = company_id
                if cf.get("twenty_company_url") != company_url:
                    update_payload.setdefault("custom_fields", {})["twenty_company_url"] = (
                        company_url
                    )

                if update_payload:
                    await self._netbox.update_tenant(
                        int(existing_tenant_id),
                        update_payload,
                    )
                    logger.info(
                        "Updated NetBox tenant %s from company %s",
                        existing_tenant_id,
                        company_id,
                    )
                else:
                    logger.debug(
                        "Tenant %s already in sync for company %s",
                        existing_tenant_id,
                        company_id,
                    )
                return

        # No existing tenant – create one
        create_payload = {
            "name": company_name,
            "slug": slug,
            "custom_fields": {
                "twenty_company_id": company_id,
                "twenty_company_url": company_url,
            },
        }
        new_tenant = await self._netbox.create_tenant(create_payload)
        new_tenant_id = str(new_tenant["id"])
        netbox_url = self._netbox_tenant_url(new_tenant_id)

        # Write back the NetBox tenant ID and deeplink to Twenty (top-level fields)
        await self._twenty.update_company(
            company_id,
            {
                "netboxTenantId": new_tenant_id,
                "netboxTenantSlug": slug,
                "netboxTenantUrl": self._links(netbox_url),
            },
        )
        logger.info("Created NetBox tenant %s for company %s", new_tenant_id, company_id)

    async def _delete_tenant_for_company(self, company: dict[str, Any]) -> None:
        tenant_id = company.get("netboxTenantId")
        if not tenant_id:
            return
        try:
            await self._netbox.delete_tenant(int(tenant_id))
            logger.info("Deleted NetBox tenant %s for company %s", tenant_id, company.get("id"))
        except Exception:
            logger.exception("Failed to delete NetBox tenant %s", tenant_id)

    # ------------------------------------------------------------------
    # People / Contacts helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _join_name(first: str, last: str) -> str:
        first = (first or "").strip()
        last = (last or "").strip()
        if first and last:
            return f"{first} {last}"
        return first or last

    @staticmethod
    def _split_name(full: str) -> tuple[str, str]:
        full = (full or "").strip()
        if not full:
            return "", ""
        parts = full.split(" ", 1)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1]

    @staticmethod
    def _primary_email(person: dict[str, Any]) -> str:
        emails = person.get("emails") or {}
        if isinstance(emails, dict):
            return emails.get("primaryEmail") or ""
        return ""

    @staticmethod
    def _primary_phone(person: dict[str, Any]) -> str:
        phones = person.get("phones") or {}
        if isinstance(phones, dict):
            return phones.get("primaryPhoneNumber") or ""
        return ""

    # ------------------------------------------------------------------
    # Twenty → NetBox (Person → Contact)
    # ------------------------------------------------------------------

    async def _sync_person_to_contact(self, person: dict[str, Any]) -> None:
        person_id = person.get("id")
        if not person_id:
            return

        name_obj = person.get("name") or {}
        if isinstance(name_obj, dict):
            first, last = name_obj.get("firstName", ""), name_obj.get("lastName", "")
        else:
            first, last = self._split_name(str(name_obj))
        nb_name = self._join_name(first, last)
        email = self._primary_email(person)
        phone = self._primary_phone(person)
        company_id = person.get("companyId") or (person.get("company") or {}).get("id")

        # Find existing NetBox contact (by linkage custom field)
        existing_contact_id = person.get("netboxContactId")
        existing = None
        if existing_contact_id and str(existing_contact_id).isdigit():
            existing = await self._netbox.get_contact(int(existing_contact_id))

        person_url = self._twenty_person_url(person_id)
        contact_data = {
            "name": nb_name,
            "email": email,
            "phone": phone,
            "custom_fields": {
                "twenty_person_id": person_id,
                "twenty_person_url": person_url,
            },
        }

        if existing:
            changed = any(
                existing.get(k) != contact_data.get(k)  # type: ignore[arg-type]
                for k in ("name", "email", "phone")
            ) or (
                existing.get("custom_fields", {}).get("twenty_person_id") != person_id
                or existing.get("custom_fields", {}).get("twenty_person_url") != person_url
            )
            if changed:
                await self._netbox.update_contact(existing["id"], contact_data)
                logger.info("Updated NetBox contact %s for person %s", existing["id"], person_id)
        else:
            new_contact = await self._netbox.create_contact(contact_data)
            new_contact_id = str(new_contact["id"])
            await self._twenty.update_person(
                person_id,
                {
                    "netboxContactId": new_contact_id,
                    "netboxContactUrl": self._links(self._netbox_contact_url(new_contact_id)),
                },
            )
            logger.info("Created NetBox contact %s for person %s", new_contact_id, person_id)
            existing = new_contact

        # Link the contact to its NetBox tenant (if the person is linked to a company)
        await self._link_contact_to_tenant(existing["id"], company_id)

    async def _link_contact_to_tenant(self, contact_id: int, company_id: str | None) -> None:
        if not company_id:
            return
        try:
            company = await self._twenty.get_company(company_id)
        except Exception:
            logger.exception("Failed to fetch company %s for contact link", company_id)
            return
        if not company:
            return
        netbox_tenant_id = company.get("netboxTenantId")
        if not netbox_tenant_id:
            return
        try:
            tenant = await self._netbox.get_tenant(int(netbox_tenant_id))
        except Exception:
            logger.warning("Tenant %s not found in NetBox; skipping contact link", netbox_tenant_id)
            return
        if not tenant:
            return
        # Avoid duplicate assignments
        existing = await self._netbox.get_contact_assignments(contact_id, "tenancy.tenant")
        if any(str(a.get("object_id")) == str(netbox_tenant_id) for a in existing):
            return
        await self._netbox.create_contact_assignment(contact_id, "tenancy.tenant", netbox_tenant_id)
        logger.info("Linked contact %s to tenant %s", contact_id, netbox_tenant_id)

    async def _delete_contact_for_person(self, person: dict[str, Any]) -> None:
        contact_id = person.get("netboxContactId")
        if not contact_id:
            return
        try:
            await self._netbox.delete_contact(int(contact_id))
            logger.info("Deleted NetBox contact %s for person %s", contact_id, person.get("id"))
        except Exception:
            logger.exception("Failed to delete NetBox contact %s", contact_id)

    # ------------------------------------------------------------------
    # NetBox → Twenty (Contact → Person)
    # ------------------------------------------------------------------

    async def _sync_contact_to_person(self, action: str, contact: dict[str, Any]) -> None:
        contact_id = contact.get("id")
        if contact_id is None:
            return
        cf = contact.get("custom_fields") or {}
        nb_person_id = cf.get("twenty_person_id")

        if action == "deleted":
            if nb_person_id:
                try:
                    await self._twenty.delete_person(nb_person_id)
                    logger.info("Deleted person %s for contact %s", nb_person_id, contact_id)
                except Exception:
                    logger.exception("Failed to delete person %s", nb_person_id)
            return

        name = contact.get("name", "")
        first, last = self._split_name(name)
        email = contact.get("email", "") or ""
        phone = contact.get("phone", "") or ""

        # Find existing person (by linkage field)
        existing = None
        if nb_person_id:
            existing = await self._twenty.get_person(nb_person_id)

        contact_url = self._netbox_contact_url(str(contact_id))
        person_data: dict[str, Any] = {
            "name": {"firstName": first, "lastName": last},
            "emails": {"primaryEmail": email} if email else {},
            "phones": {"primaryPhoneNumber": phone} if phone else {},
            "netboxContactId": str(contact_id),
            "netboxContactUrl": self._links(contact_url),
        }

        if existing:
            needs_update = (
                self._primary_email(existing) != email
                or self._primary_phone(existing) != phone
                or self._join_name(
                    (existing.get("name") or {}).get("firstName", ""),
                    (existing.get("name") or {}).get("lastName", ""),
                )
                != name
                or str(existing.get("netboxContactId")) != str(contact_id)
                or self._links_url(existing.get("netboxContactUrl")) != contact_url
            )
            if needs_update:
                await self._twenty.update_person(existing["id"], person_data)
                logger.info("Updated person %s for contact %s", existing["id"], contact_id)
        else:
            new_person = await self._twenty.create_person(person_data)
            new_person_id = new_person.get("id")
            await self._netbox.update_contact(
                int(contact_id),
                {
                    "custom_fields": {
                        "twenty_person_id": new_person_id,
                        "twenty_person_url": self._twenty_person_url(new_person_id),
                    }
                },
            )
            logger.info("Created person %s for contact %s", new_person_id, contact_id)
            existing = new_person

        # Link the person to its company (via the contact's NetBox tenant)
        await self._link_person_to_company(existing.get("id"), int(contact_id))

    async def _link_person_to_company(self, person_id: str | None, contact_id: int) -> None:
        if not person_id:
            return
        assignments = await self._netbox.get_contact_assignments(contact_id, "tenancy.tenant")
        if not assignments:
            return
        tenant_id = str(assignments[0].get("object_id"))
        tenant = await self._netbox.get_tenant(int(tenant_id))
        if not tenant:
            return
        company_id = (tenant.get("custom_fields") or {}).get("twenty_company_id")
        if not company_id:
            return
        try:
            company = await self._twenty.get_company(company_id)
        except Exception:
            logger.exception("Failed to fetch company %s for person link", company_id)
            return
        if not company:
            return
        if str(company.get("id")) == str(
            (await self._twenty.get_person(person_id) or {}).get("companyId")
        ):
            return
        await self._twenty.update_person(person_id, {"companyId": company["id"]})
        logger.info("Linked person %s to company %s", person_id, company["id"])

    async def _delete_person_for_contact(self, contact: dict[str, Any]) -> None:
        cf = contact.get("custom_fields") or {}
        nb_person_id = cf.get("twenty_person_id")
        if not nb_person_id:
            return
        try:
            await self._twenty.delete_person(nb_person_id)
            logger.info("Deleted person %s for contact %s", nb_person_id, contact.get("id"))
        except Exception:
            logger.exception("Failed to delete person %s", nb_person_id)

    # ------------------------------------------------------------------
    # NetBox → Twenty
    # ------------------------------------------------------------------

    async def handle_netbox_event(self, payload: dict[str, Any]) -> None:
        # NetBox v4.7 webhooks deliver the model under "object_type" (and the
        # action under "event"). Older/other versions may use "model"/"action".
        raw_model = payload.get("model") or payload.get("object_type", "")
        action = payload.get("action") or payload.get("event", "")
        # Strip the app label so we match on the bare model name.
        model = raw_model.split(".", 1)[-1] if "." in raw_model else raw_model
        data = payload.get("data", {})

        match model:
            case "tenant":
                await self._sync_tenant_to_company(action, data)
            case "contact":
                await self._sync_contact_to_person(action, data)
            case "vrf" | "prefix":
                await self._sync_infra_to_netbox_resource(model, action, data)
            case _:
                logger.debug("Ignoring unhandled NetBox model: %s", raw_model)

    async def _sync_tenant_to_company(self, action: str, tenant: dict[str, Any]) -> None:
        company_id = (tenant.get("custom_fields") or {}).get("twenty_company_id")

        if action == "deleted":
            if company_id:
                await self._twenty.update_company(
                    company_id,
                    {
                        "netboxTenantId": None,
                        "netboxTenantSlug": None,
                        "netboxTenantUrl": self._links(""),
                    },
                )
                logger.info("Cleared NetBox IDs on company %s after tenant deletion", company_id)
            return

        tenant_name = tenant.get("name", "")
        tenant_slug = tenant.get("slug", "")
        tenant_id = str(tenant.get("id", ""))
        tenant_url = self._netbox_tenant_url(tenant_id)

        if company_id:
            # Idempotency check
            existing = await self._twenty.get_company(company_id)
            if existing:
                needs_update = (
                    existing.get("netboxTenantId") != tenant_id
                    or existing.get("netboxTenantSlug") != tenant_slug
                    or self._links_url(existing.get("netboxTenantUrl")) != tenant_url
                )
                if needs_update:
                    await self._twenty.update_company(
                        company_id,
                        {
                            "netboxTenantId": tenant_id,
                            "netboxTenantSlug": tenant_slug,
                            "netboxTenantUrl": self._links(tenant_url),
                        },
                    )
                    logger.info("Updated company %s with NetBox tenant %s", company_id, tenant_id)
                else:
                    logger.debug("Company %s already synced with tenant %s", company_id, tenant_id)
            return

        # No company linked – create a new one (custom fields are top-level)
        new_company = await self._twenty.create_company(
            {
                "name": tenant_name,
                "netboxTenantId": tenant_id,
                "netboxTenantSlug": tenant_slug,
                "netboxTenantUrl": self._links(tenant_url),
            }
        )
        # Write back the company ID and deeplink to NetBox
        company_url = self._twenty_company_url(new_company["id"])
        await self._netbox.update_tenant(
            tenant.get("id"),
            {
                "custom_fields": {
                    "twenty_company_id": new_company["id"],
                    "twenty_company_url": company_url,
                },
            },
        )
        logger.info("Created company %s for NetBox tenant %s", new_company["id"], tenant_id)

    def _netbox_resource_url(self, model: str, resource_id: str) -> str:
        base = self._settings.netbox_url.rstrip("/")
        if model == "vrf":
            return f"{base}/ipam/vrfs/{resource_id}/"
        return f"{base}/ipam/prefixes/{resource_id}/"

    async def _sync_infra_to_netbox_resource(
        self, model: str, action: str, resource: dict[str, Any]
    ) -> None:
        """Sync VRF/Prefix → Twenty NetboxResource custom object."""
        resource_type = "VRF" if model == "vrf" else "Prefix"
        netbox_id = str(resource.get("id", ""))
        tenant = resource.get("tenant", {})
        tenant_id = str(tenant.get("id", "")) if isinstance(tenant, dict) else str(tenant)

        netbox_url = self._netbox_resource_url(model, netbox_id)

        # Find existing record
        existing = await self._twenty.get_netbox_resources(filters={"netboxid": netbox_id})
        existing_record = existing[0] if existing else None

        if action == "deleted":
            if existing_record:
                await self._twenty._delete(f"/rest/netboxresources/{existing_record['id']}")
                logger.info("Deleted NetboxResource %s", existing_record["id"])
            return

        name = (
            resource.get("name")
            or resource.get("prefix")
            or resource.get("cidr")
            or f"{resource_type}-{netbox_id}"
        )
        prefix_cidr = resource.get("prefix") or resource.get("cidr") or ""

        # Custom-object fields are top-level; LINKS URL field needs object form.
        record_data: dict[str, Any] = {
            "name": name,
            "resourcetype": resource_type,
            "prefixcidr": prefix_cidr,
            "netboxid": netbox_id,
            "companyid": tenant_id,
            "netboxurl": self._links(netbox_url),
        }

        if existing_record:
            # Idempotency
            changed = any(
                existing_record.get(k) != v for k, v in record_data.items() if k != "companyid"
            )
            if changed:
                await self._twenty.update_netbox_resource(existing_record["id"], record_data)
                logger.info("Updated NetboxResource %s", existing_record["id"])
        else:
            await self._twenty.create_netbox_resource(record_data)
            logger.info("Created NetboxResource for %s %s", resource_type, netbox_id)

    # ------------------------------------------------------------------
    # Periodic / initial reconciliation (duplicate detection)
    # ------------------------------------------------------------------

    async def reconcile_all(self) -> None:
        """Full bidirectional reconciliation for all synced entities.

        Used for initial sync and to catch changes missed while the middleware
        was offline. Matching priority: linkage custom field, then natural key
        (email/name for people, slug/name for companies, netbox id for infra).
        """
        await self.reconcile_companies()
        await self.reconcile_people()
        await self.reconcile_resources()

    async def reconcile_companies(self) -> None:
        companies = await self._twenty.get_companies()
        tenants = await self._netbox.get_tenants()
        tenant_by_cf = {
            t.get("custom_fields", {}).get("twenty_company_id"): t
            for t in tenants
            if t.get("custom_fields", {}).get("twenty_company_id")
        }
        tenant_by_slug = {t["slug"]: t for t in tenants}
        tenant_by_name = {t["name"].lower(): t for t in tenants}
        matched_tenants = set()

        for company in companies:
            cid = company.get("id")
            if not cid:
                continue
            tenant = tenant_by_cf.get(cid)
            if not tenant:
                slug = slugify(company.get("name", ""))
                tenant = tenant_by_slug.get(slug) or tenant_by_name.get(
                    company.get("name", "").lower()
                )
            if tenant:
                matched_tenants.add(tenant["id"])
                if str(
                    tenant.get("custom_fields", {}).get("twenty_company_id")
                ) != cid or not company.get("netboxTenantId"):
                    await self._netbox.update_tenant(
                        tenant["id"],
                        {
                            "custom_fields": {
                                "twenty_company_id": cid,
                                "twenty_company_url": self._twenty_company_url(cid),
                            }
                        },
                    )
                    await self._twenty.update_company(
                        cid,
                        {
                            "netboxTenantId": str(tenant["id"]),
                            "netboxTenantSlug": tenant["slug"],
                            "netboxTenantUrl": self._links(
                                self._netbox_tenant_url(str(tenant["id"]))
                            ),
                        },
                    )
                    logger.info("Reconciled company %s <-> tenant %s", cid, tenant["id"])
            else:
                await self._sync_company_to_tenant(company)

        for tenant in tenants:
            if tenant["id"] in matched_tenants:
                continue
            cfid = tenant.get("custom_fields", {}).get("twenty_company_id")
            company = next((c for c in companies if c.get("id") == cfid), None) if cfid else None
            if not company:
                company = next(
                    (c for c in companies if str(c.get("netboxTenantId")) == str(tenant["id"])),
                    None,
                )
            if not company:
                slug = tenant["slug"]
                company = next(
                    (c for c in companies if slugify(c.get("name", "")) == slug), None
                ) or next(
                    (c for c in companies if c.get("name", "").lower() == tenant["name"].lower()),
                    None,
                )
            if company:
                await self._netbox.update_tenant(
                    tenant["id"],
                    {
                        "custom_fields": {
                            "twenty_company_id": company["id"],
                            "twenty_company_url": self._twenty_company_url(company["id"]),
                        }
                    },
                )
                await self._twenty.update_company(
                    company["id"],
                    {
                        "netboxTenantId": str(tenant["id"]),
                        "netboxTenantSlug": tenant["slug"],
                        "netboxTenantUrl": self._links(self._netbox_tenant_url(str(tenant["id"]))),
                    },
                )
                logger.info("Reconciled tenant %s <-> company %s", tenant["id"], company["id"])
            else:
                await self._sync_tenant_to_company("object_updated", tenant)

    async def reconcile_people(self) -> None:
        contacts = await self._netbox.get_contacts()
        people = await self._twenty.get_people()

        people_by_id = {p["id"]: p for p in people}
        people_by_email: dict[str, dict[str, Any]] = {}
        people_by_name: dict[str, dict[str, Any]] = {}
        for p in people:
            email = self._primary_email(p)
            if email:
                people_by_email[email.lower()] = p
            name = self._join_name(
                (p.get("name") or {}).get("firstName", ""),
                (p.get("name") or {}).get("lastName", ""),
            )
            if name:
                people_by_name[name.lower()] = p

        matched_people = set()
        for contact in contacts:
            cid = contact.get("id")
            if cid is None:
                continue
            cf = contact.get("custom_fields") or {}
            person_id = cf.get("twenty_person_id")
            person = people_by_id.get(person_id) if person_id else None
            if not person:
                email = (contact.get("email") or "").lower()
                person = people_by_email.get(email) if email else None
            if not person:
                name = (contact.get("name") or "").lower()
                person = people_by_name.get(name) if name else None
            try:
                await self._sync_contact_to_person("object_updated", contact)
            except Exception:
                logger.exception("Reconcile failed for contact %s", cid)
            if person:
                matched_people.add(person["id"])

        for person in people:
            if person["id"] in matched_people:
                continue
            pid = person.get("netboxContactId")
            if pid and any(str(c.get("id")) == str(pid) for c in contacts):
                continue
            try:
                await self._sync_person_to_contact(person)
            except Exception:
                logger.exception("Reconcile failed for person %s", person.get("id"))

    async def reconcile_resources(self) -> None:
        """NetBox → Twenty reconciliation for VRFs/Prefixes (by netbox id)."""
        resources = await self._twenty.get_netbox_resources()
        resource_by_netbox_id = {r.get("netboxid"): r for r in resources if r.get("netboxid")}

        vrfs = await self._netbox.get_vrfs()
        prefixes = await self._netbox.get_prefixes()
        for vrf in vrfs:
            if str(vrf.get("id")) not in resource_by_netbox_id:
                try:
                    await self._sync_infra_to_netbox_resource("vrf", "object_created", vrf)
                except Exception:
                    logger.exception("Reconcile failed for VRF %s", vrf.get("id"))
        for prefix in prefixes:
            if str(prefix.get("id")) not in resource_by_netbox_id:
                try:
                    await self._sync_infra_to_netbox_resource("prefix", "object_created", prefix)
                except Exception:
                    logger.exception("Reconcile failed for prefix %s", prefix.get("id"))

    # ------------------------------------------------------------------
    # Worker task entry points (called by saq workers)
    # ------------------------------------------------------------------


def _load_settings() -> Settings:
    return Settings()


async def process_twenty_event(ctx: dict[str, Any], job: dict[str, Any]) -> None:
    """saq task: process a Twenty CRM webhook event."""
    engine = SyncEngine(_load_settings())
    try:
        await engine.handle_twenty_event(job["payload"])
    finally:
        await engine.close()


async def process_netbox_event(ctx: dict[str, Any], job: dict[str, Any]) -> None:
    """saq task: process a NetBox webhook event."""
    engine = SyncEngine(_load_settings())
    try:
        await engine.handle_netbox_event(job["payload"])
    finally:
        await engine.close()
