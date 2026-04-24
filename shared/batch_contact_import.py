from datetime import datetime, timezone

from phone_identity import build_phone_identity
from supabase_client import SupabaseClient
from wati_client import WatiClient


class BatchContactImport:
    def __init__(self, supabase_client: SupabaseClient, wati_client: WatiClient, db_tenant_id: int):
        self.supabase_client = supabase_client
        self.wati_client = wati_client
        self.db_tenant_id = db_tenant_id

    def run(self, limit: int = None) -> dict:
        client = self.supabase_client.get_client()
        contacts = self.wati_client.get_contacts(max_pages=limit)
        total = len(contacts)
        batch_size = 100
        upserted_staging = 0
        now = datetime.now(timezone.utc).isoformat()

        # Create an import job to track this batch
        job_result = (
            client.schema("ops").table("import_jobs").insert({
                "tenant_id": self.db_tenant_id,
                "job_type": "contact_import",
                "status": "running",
                "total_count": total,
                "started_at": now,
            }).execute()
        )
        batch_id = job_result.data[0]["job_id"]
        print(f"Import job created: {batch_id}")

        try:
            # Step 1: Upsert raw contacts into staging in batches of 100
            for batch_start in range(0, total, batch_size):
                batch = contacts[batch_start:batch_start + batch_size]
                batch_end = batch_start + len(batch)
                print(f"Staging contacts {batch_start + 1}-{batch_end} of {total}...")

                staging_rows = []
                for c in batch:
                    if c.get("id"):
                        staging_rows.append({
                            "tenant_id": self.db_tenant_id,
                            "batch_id": batch_id,
                            "wati_contact_id": str(c["id"]),
                            "raw_data": c,
                            "last_seen_at": now,
                        })

                if staging_rows:
                    client.schema("staging").table("contacts_raw_data").upsert(
                        staging_rows,
                        on_conflict="tenant_id,wati_contact_id",
                    ).execute()
                    upserted_staging += len(staging_rows)

            # Step 2: Promote staged contacts to canonical via DB-side function
            print(f"Promoting {upserted_staging} contacts to canonical...")
            rpc_result = client.schema("public").rpc(
                "process_contacts_to_canonical",
                {"p_batch_id": batch_id},
            ).execute()
            promotion = rpc_result.data
            upserted_canonical = promotion.get("contacts_processed", 0)

            # Step 3: Enrich canonical contacts with smart identity fields
            self._enrich_identity_fields(client, batch_id)

            # Mark job completed
            client.schema("ops").table("import_jobs").update({
                "status": "completed",
                "processed_count": upserted_canonical,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("job_id", batch_id).execute()

        except Exception as exc:
            client.schema("ops").table("import_jobs").update({
                "status": "failed",
                "error_message": str(exc),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("job_id", batch_id).execute()
            raise

        return {
            "batch_id": batch_id,
            "total": total,
            "upserted_staging": upserted_staging,
            "upserted_canonical": upserted_canonical,
        }

    def _load_default_phone_region(self, client) -> str | None:
        result = (
            client.table("tenant_connectors")
            .select("default_phone_region")
            .eq("tenant_id", self.db_tenant_id)
            .eq("provider", "wati")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return rows[0].get("default_phone_region")

    def _enrich_identity_fields(self, client, batch_id: str) -> None:
        default_region = self._load_default_phone_region(client)

        # Load all staged contacts for this batch, paginating past the 1000-row limit
        staged = []
        page_size = 1000
        offset = 0
        while True:
            page = (
                client.schema("staging").table("contacts_raw_data")
                .select("wati_contact_id, raw_data")
                .eq("batch_id", batch_id)
                .range(offset, offset + page_size - 1)
                .execute()
            ).data or []
            if not page:
                break
            staged.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

        if not staged:
            return

        print(f"Enriching {len(staged)} contacts with phone identity fields...")

        for row in staged:
            raw_data = row["raw_data"]
            wati_contact_id = row["wati_contact_id"]
            raw_phone = raw_data.get("wAid") or raw_data.get("phone") or raw_data.get("whatsappNumber")

            identity = build_phone_identity(
                raw_input=raw_phone,
                default_region=default_region,
                wati_contact_id=wati_contact_id,
            )

            # Find the canonical contact by wati_contact_id
            contact_row = (
                client.schema("canonical").table("contacts")
                .select("contact_id")
                .eq("tenant_id", self.db_tenant_id)
                .eq("wati_contact_id", wati_contact_id)
                .limit(1)
                .execute()
            ).data or []

            if not contact_row:
                continue

            contact_id = contact_row[0]["contact_id"]

            update_fields = {
                "phone_identity_key": identity.phone_identity_key,
                "phone_e164": identity.phone_e164,
                "normalization_status": identity.normalization_status,
                "normalization_region": identity.normalization_region,
                "wati_lookup_phone": identity.wati_lookup_phone,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if identity.raw_input:
                update_fields["phone_raw_primary"] = identity.raw_input

            client.schema("canonical").table("contacts").update(
                update_fields
            ).eq("contact_id", contact_id).execute()

            # Update provider key with lookup phone
            client.schema("canonical").table("contact_provider_keys").update({
                "provider_phone": identity.wati_lookup_phone or raw_phone,
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("tenant_id", self.db_tenant_id).eq("provider_contact_id", wati_contact_id).execute()
