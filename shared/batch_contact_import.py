from datetime import datetime, timezone

from contact_identity_repair import ContactIdentityRepair
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
            repair_summary = ContactIdentityRepair(self.supabase_client, self.db_tenant_id).run(
                batch_id=batch_id,
                dry_run=False,
            )
            print(
                "Identity repair complete: "
                f"examined={repair_summary['contacts_examined']} "
                f"updates={repair_summary['contact_updates']} "
                f"provider_keys={repair_summary['provider_key_updates']} "
                f"merged={repair_summary['merged_contacts']}"
            )

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
