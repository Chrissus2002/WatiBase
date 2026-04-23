import os
import signal
import sys
import time
from datetime import datetime, timezone

from supabase_client import SupabaseClient
from wati_client import WatiClient


class BatchMessageImport:
    def __init__(self, supabase_client: SupabaseClient, wati_client: WatiClient, db_tenant_id: int):
        self.supabase_client = supabase_client
        self.wati_client = wati_client
        self.db_tenant_id = db_tenant_id

    def run(self, limit: int = None, resume_job_id: str = None) -> dict:
        client = self.supabase_client.get_client()
        offset = 0
        page_size = 1000
        all_contacts = []

        pages_fetched = 0
        while True:
            if limit is not None and pages_fetched >= limit:
                break

            query = (client.schema("canonical")
            .table("contacts")
            .select("phone_raw")
            .eq("tenant_id", self.db_tenant_id)
            .order("phone_normalized")
            .range(offset, offset + page_size - 1)
            .execute())

            if not query.data:
                break
            all_contacts.extend(query.data)
            pages_fetched += 1

            if len(query.data) < page_size:
                break

            offset += page_size

        total = len(all_contacts)
        print(f"Fetched {total} contacts")
        batch_size = 100
        upserted_staging = 0
        now = datetime.now(timezone.utc).isoformat()
        start_from = 0

        if resume_job_id:
            # Reuse existing job — read processed_count to know where to resume
            batch_id = resume_job_id
            job_row = (
                client.schema("ops").table("import_jobs")
                .select("processed_count")
                .eq("job_id", batch_id)
                .single()
                .execute()
            )
            start_from = job_row.data.get("processed_count") or 0
            client.schema("ops").table("import_jobs").update({
                "status": "running",
                "error_message": None,
                "total_count": total,
                "subprocess_pid": os.getpid(),
            }).eq("job_id", batch_id).execute()
            print(f"Resuming import job: {batch_id} from contact {start_from + 1}")
        else:
            # Create a new import job
            job_result = (
                client.schema("ops").table("import_jobs").insert({
                    "tenant_id": self.db_tenant_id,
                    "job_type": "message_backfill",
                    "status": "running",
                    "total_count": total,
                    "started_at": now,
                }).execute()
            )
            batch_id = job_result.data[0]["job_id"]
            client.schema("ops").table("import_jobs").update({
                "subprocess_pid": os.getpid(),
            }).eq("job_id", batch_id).execute()
            print(f"Import job created: {batch_id}")

        # Register signal handler to mark job as stopped on kill
        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            print(f"\nReceived {sig_name}, marking job as stopped...", flush=True)
            client.schema("ops").table("import_jobs").update({
                "status": "failed",
                "error_message": f"Stopped by {sig_name}",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("job_id", batch_id).execute()
            sys.exit(1)

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        try:
            for idx, contact in enumerate(all_contacts, 1):
                # Skip contacts already processed in a previous run
                if idx <= start_from:
                    continue

                phone = contact.get("phone_raw")
                if not phone:
                    print(f"[{idx}/{total}] Skipping contact with no phone_raw", flush=True)
                    continue

                print(f"[{idx}/{total}] Fetching messages for {phone}...", flush=True)
                try:
                    result = self.wati_client.get_messages(phone)
                    messages = result.get(phone, [])
                except Exception as exc:
                    print(f"  Error fetching messages for {phone}: {exc}", flush=True)
                    continue

                # Upsert messages into staging in batches of 100
                for batch_start in range(0, len(messages), batch_size):
                    batch = messages[batch_start:batch_start + batch_size]

                    staging_rows = []
                    for msg in batch:
                        msg_id = msg.get("id")
                        if not msg_id:
                            continue
                        staging_rows.append({
                            "tenant_id": self.db_tenant_id,
                            "batch_id": batch_id,
                            "wati_message_id": str(msg_id),
                            "conversation_id": msg.get("conversationId") or str(phone),
                            "raw_data": msg,
                        })

                    if staging_rows:
                        client.schema("staging").table("messages_raw_data").upsert(
                            staging_rows,
                            on_conflict="tenant_id,wati_message_id",
                        ).execute()
                        upserted_staging += len(staging_rows)

                # Update progress after each contact (absolute index)
                client.schema("ops").table("import_jobs").update({
                    "processed_count": idx,
                }).eq("job_id", batch_id).execute()

                time.sleep(2)

            # Promote staged messages to canonical via DB-side function
            print(f"Promoting {upserted_staging} messages to canonical...")
            rpc_result = client.schema("public").rpc(
                "process_messages_to_canonical",
                {"p_batch_id": batch_id},
            ).execute()
            promotion = rpc_result.data
            upserted_canonical = promotion.get("messages_processed", 0)

            # Mark job completed
            client.schema("ops").table("import_jobs").update({
                "status": "completed",
                "processed_count": total,
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
            "skipped": start_from,
        }
