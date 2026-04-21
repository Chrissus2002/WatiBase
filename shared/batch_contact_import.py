import re
from datetime import datetime, timezone

from supabase_client import SupabaseClient
from wati_client import WatiClient


class BatchContactImport:
    def __init__(self, supabase_client: SupabaseClient, wati_client: WatiClient, db_tenant_id: int):
        self.supabase_client = supabase_client
        self.wati_client = wati_client
        self.db_tenant_id = db_tenant_id

    def run(self) -> dict:
        client = self.supabase_client.get_client()
        contacts = self.wati_client.get_contacts()
        total = len(contacts)
        batch_size = 100
        upserted_staging = 0
        upserted_canonical = 0
        skipped = 0
        now = datetime.now(timezone.utc).isoformat()

        # Step 1 & 2: Upsert into staging.contacts_raw_data in batches of 100
        for batch_start in range(0, total, batch_size):
            batch = contacts[batch_start:batch_start + batch_size]
            batch_end = batch_start + len(batch)
            print(f"Importing contacts {batch_start + 1}-{batch_end} of {total}...")

            staging_rows = [
                {
                    "tenant_id": self.db_tenant_id,
                    "wati_contact_id": str(c["id"]),
                    "raw_data": c,
                    "last_seen_at": now,
                }
                for c in batch
            ]

            client.schema("staging").table("contacts_raw_data").upsert(
                staging_rows,
                on_conflict="tenant_id,wati_contact_id",
            ).execute()
            upserted_staging += len(staging_rows)

        # Step 3 & 4: Promote each contact into canonical.contacts and contact_provider_keys
        for c in contacts:
            raw_phone = c.get("wAid") or c.get("phone") or c.get("whatsappNumber")
            if not raw_phone:
                print(f"Warning: skipping contact id={c.get('id')} — no recoverable phone")
                skipped += 1
                continue

            phone_normalized = re.sub(r"\D", "", str(raw_phone))
            if not phone_normalized:
                print(f"Warning: skipping contact id={c.get('id')} — phone normalizes to empty string")
                skipped += 1
                continue

            name = c.get("fullName") or c.get("name")
            wati_contact_id = str(c["id"])

            canonical_row = {
                "tenant_id": self.db_tenant_id,
                "phone_normalized": phone_normalized,
                "phone_raw": str(raw_phone),
                "name": name,
                "source": "wati",
                "wati_contact_id": wati_contact_id,
                "is_canonical": True,
            }

            client.schema("canonical").table("contacts").upsert(
                canonical_row,
                on_conflict="tenant_id,phone_normalized",
            ).execute()
            upserted_canonical += 1

            # Fetch contact_id for the just-upserted canonical contact
            result = (
                client.schema("canonical")
                .table("contacts")
                .select("contact_id")
                .eq("tenant_id", self.db_tenant_id)
                .eq("phone_normalized", phone_normalized)
                .single()
                .execute()
            )
            contact_id = result.data["contact_id"]

            provider_key_row = {
                "contact_id": contact_id,
                "tenant_id": self.db_tenant_id,
                "provider": "wati",
                "provider_contact_id": wati_contact_id,
                "provider_phone": str(raw_phone),
            }

            client.schema("canonical").table("contact_provider_keys").upsert(
                provider_key_row,
                on_conflict="tenant_id,provider,provider_contact_id",
            ).execute()

        return {
            "total": total,
            "upserted_staging": upserted_staging,
            "upserted_canonical": upserted_canonical,
            "skipped": skipped,
        }
