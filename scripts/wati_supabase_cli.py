#!/usr/bin/env python3
"""
CLI-first admin surface for the Wati -> Supabase connector.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import os
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from dotenv import load_dotenv
from pathlib import Path
from typing import Any

from supabase_client import SupabaseClient
from wati_client import WatiClient
from batch_contact_import import BatchContactImport
from batch_message_import import BatchMessageImport
from contact_identity_repair import ContactIdentityRepair

load_dotenv()
client: SupabaseClient | None = None


def runtime_supabase_url() -> str | None:
    return os.getenv("SUPABASE_URL")


def runtime_supabase_key() -> str | None:
    return os.getenv("SUPABASE_KEY")


def get_supabase_client() -> SupabaseClient:
    global client
    if client is not None:
        return client

    supabase_url = runtime_supabase_url()
    supabase_key = runtime_supabase_key()
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")

    client = SupabaseClient(supabase_url, supabase_key)
    return client


def get_wati_client(tenant_id: int) -> WatiClient:
    """Create a WatiClient from DB credentials for the given tenant."""
    return WatiClient.from_supabase(get_supabase_client(), tenant_id)

@dataclass
class StatusSnapshot:
    supabase_url: str
    supabase_key: str
    tenant_id: int
    tenant_name: str
    tenant_slug: str | None
    wati_api_url: str | None
    wati_api_token: str | None
    default_phone_region: str | None
    contact_count: int
    message_count: int

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wati-supabase",
        description="CLI admin surface for the Wati -> Supabase connector",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Set up Supabase and Wati credentials in one flow")
    setup_parser.set_defaults(handler=handle_setup)

    test_parser = subparsers.add_parser("test-connection", help="Verify Supabase connectivity")
    test_parser.set_defaults(handler=handle_test_connection)

    tenants_parser = subparsers.add_parser("tenants", help="List all configured tenants")
    tenants_parser.set_defaults(handler=handle_tenants)

    status_parser = subparsers.add_parser("status", help="Show connector status for a tenant")
    status_parser.add_argument("--tenant-id", type=int, required=True, help="Tenant ID")
    status_parser.set_defaults(handler=handle_status)

    watch_parser = subparsers.add_parser("watch", help="Poll selected status surfaces")
    watch_subparsers = watch_parser.add_subparsers(dest="watch_command", required=True)
    watch_status_parser = watch_subparsers.add_parser("status", help="Poll connector status")
    watch_status_parser.add_argument("--tenant-id", type=int, required=True, help="Tenant ID")
    watch_status_parser.add_argument("--interval", type=int, default=10, help="Refresh interval in seconds")
    watch_status_parser.set_defaults(handler=handle_watch_status)

    sync_parser = subparsers.add_parser("sync", help="Import contacts from Wati into Supabase")
    sync_subparsers = sync_parser.add_subparsers(dest="sync_command", required=True)
    sync_contacts_parser = sync_subparsers.add_parser("contacts", help="Sync contacts from Wati")
    sync_contacts_parser.add_argument("--tenant-id", type=int, required=True, help="DB tenant ID")
    sync_contacts_parser.add_argument("--limit", type=int, default=None, help="Max number of Wati contact pages to import (for testing)")
    sync_contacts_parser.set_defaults(handler=handle_sync_contacts)

    backfill_parser = subparsers.add_parser("backfill", help="Import message history for all synced contacts")
    backfill_parser.add_argument("--tenant-id", type=int, required=True, help="DB tenant ID")
    backfill_parser.add_argument("--limit", type=int, default=None, help="Max pages of canonical contacts to process (for testing)")
    backfill_parser.add_argument("--resume", type=str, default=None, help="Resume a failed/interrupted job by job_id")
    backfill_parser.add_argument("--_background", action="store_true", default=False, help=argparse.SUPPRESS)
    backfill_parser.set_defaults(handler=handle_backfill_messages)

    logs_parser = subparsers.add_parser("logs", help="Inspect connector logs")
    logs_parser.add_argument("--list", action="store_true", help="List log files with copy-pasteable commands")
    logs_parser.add_argument("--file", default=None, help="Display a specific log file")
    logs_parser.add_argument("--tail", action="store_true", help="Show only the last 50 lines (use with --file)")
    logs_parser.set_defaults(handler=handle_logs)

    repair_parser = subparsers.add_parser("repair", help="Run connector repair operations")
    repair_subparsers = repair_parser.add_subparsers(dest="repair_command", required=True)
    repair_contacts_parser = repair_subparsers.add_parser("contacts", help="Repair canonical contact identity state")
    repair_contacts_parser.add_argument("--tenant-id", type=int, required=True, help="DB tenant ID")
    repair_contacts_parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to the database")
    repair_contacts_parser.set_defaults(handler=handle_repair_contacts)

    to_file_parser = subparsers.add_parser("to-file", help="Download Wati data to local files")
    to_file_subparsers = to_file_parser.add_subparsers(dest="to_file_command", required=True)

    to_file_contacts_parser = to_file_subparsers.add_parser("contacts", help="Download all contacts to a JSON file")
    to_file_contacts_parser.add_argument("--tenant-id", type=int, required=True, help="DB tenant ID")
    to_file_contacts_parser.add_argument("--output", default="contacts.json", help="Output file path (default: contacts.json)")
    to_file_contacts_parser.set_defaults(handler=handle_to_file_contacts)

    to_file_messages_parser = to_file_subparsers.add_parser("messages", help="Download messages to local JSON files")
    to_file_messages_parser.add_argument("--tenant-id", type=int, required=True, help="DB tenant ID")
    to_file_messages_group = to_file_messages_parser.add_mutually_exclusive_group(required=True)
    to_file_messages_group.add_argument("--phone", help="Phone number of a single contact")
    to_file_messages_group.add_argument("--all", action="store_true", help="Download messages for all contacts")
    to_file_messages_parser.add_argument("--output", default=None, help="Output file path for single contact (default: messages_<phone>.json)")
    to_file_messages_parser.add_argument("--output-dir", default="messages", help="Output directory for --all (default: messages/)")
    to_file_messages_parser.set_defaults(handler=handle_to_file_messages)

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)

def handle_test_connection(args: argparse.Namespace) -> int:
    try:
        response = get_supabase_client().test_connection()

        if response:
            print("Supabase connection OK", flush=True)
            return 0
        else:
            print("Supabase connection failed", flush=True)
            return 1
    except Exception as e:
        print(f"Connection error: {e}", flush=True)
        return 1


def handle_tenants(args: argparse.Namespace) -> int:
    try:
        db = get_supabase_client().get_client()
        tenants = (
            db.table("tenant")
            .select("tenant_id, name, slug")
            .order("tenant_id")
            .execute()
        )
        rows = tenants.data or []
        if not rows:
            print("No tenants configured.", flush=True)
            return 0

        # Header
        print(f"{'ID':<10} {'Name':<30} {'Slug':<20}", flush=True)
        print(f"{'-'*10} {'-'*30} {'-'*20}", flush=True)
        for row in rows:
            tid = row["tenant_id"]
            name = row.get("name") or "-"
            slug = row.get("slug") or "-"
            print(f"{tid:<10} {name:<30} {slug:<20}", flush=True)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1


def load_status_snapshot(tenant_id: int) -> StatusSnapshot:
    supabase_url = runtime_supabase_url()
    supabase_key = runtime_supabase_key()
    db_client = get_supabase_client()
    tenant_response = (
        db_client.get_client().table("tenant")
        .select("tenant_id, name, slug")
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    tenant_rows = tenant_response.data or []
    if not tenant_rows:
        raise ValueError(f"Tenant {tenant_id} not found")

    tenant = tenant_rows[0]

    connector_response = (
        db_client.get_client().table("tenant_connectors")
        .select("wati_api_url, wati_api_token, default_phone_region")
        .eq("tenant_id", tenant_id)
        .eq("provider", "wati")
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    connector_rows = connector_response.data or []
    connector = connector_rows[0] if connector_rows else {}

    contact_count_resp = (
        db_client.get_client().schema("canonical").table("contacts")
        .select("contact_id", count="exact")
        .eq("tenant_id", tenant_id)
        .limit(0)
        .execute()
    )
    message_count_resp = (
        db_client.get_client().schema("canonical").table("messages")
        .select("message_id", count="exact")
        .eq("tenant_id", tenant_id)
        .limit(0)
        .execute()
    )

    return StatusSnapshot(
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        tenant_id=tenant["tenant_id"],
        tenant_name=tenant.get("name") or f"Tenant {tenant_id}",
        tenant_slug=tenant.get("slug"),
        wati_api_url=connector.get("wati_api_url"),
        wati_api_token=connector.get("wati_api_token"),
        default_phone_region=connector.get("default_phone_region"),
        contact_count=contact_count_resp.count or 0,
        message_count=message_count_resp.count or 0,
    )


def mask_secret(value: str | None, suffix: int = 4) -> str:
    if not value:
        return "not configured"
    if len(value) <= suffix:
        return "*" * len(value)
    return f"***{value[-suffix:]}"


def format_status(snapshot: StatusSnapshot) -> str:
    lines: list[str] = []
    lines.append("Supabase")
    lines.append(f"  URL:   {snapshot.supabase_url}")
    lines.append(f"  Key:   {mask_secret(snapshot.supabase_key)}")
    lines.append("")
    lines.append("Tenant")
    lines.append(f"  ID:    {snapshot.tenant_id}")
    lines.append(f"  Name:  {snapshot.tenant_name}")
    lines.append(f"  Slug:  {snapshot.tenant_slug or '-'}")
    lines.append("")
    lines.append("Wati Connector")
    lines.append(f"  URL:    {snapshot.wati_api_url or 'not configured'}")
    lines.append(f"  Token:  {mask_secret(snapshot.wati_api_token)}")
    lines.append(f"  Region: {snapshot.default_phone_region or 'not set'}")
    lines.append("")
    lines.append("Data")
    lines.append(f"  Contacts: {snapshot.contact_count:,}")
    lines.append(f"  Messages: {snapshot.message_count:,}")

    return "\n".join(lines) + "\n"


def handle_status(args: argparse.Namespace) -> int:
    try:
        snapshot = load_status_snapshot(args.tenant_id)
    except Exception as exc:
        print(f"Status failed: {exc}", flush=True)
        return 1

    sys.stdout.write(format_status(snapshot))
    sys.stdout.flush()
    return 0


def handle_watch_status(args: argparse.Namespace) -> int:
    try:
        while True:
            if sys.stdout.isatty():
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
            code = handle_status(argparse.Namespace(tenant_id=args.tenant_id))
            if code != 0:
                return code
            print(flush=True)
            print(f"Refreshing every {args.interval}s. Ctrl+C to stop.", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped watch.", flush=True)
        return 0
    
def _update_env(updates: dict[str, str]) -> None:
    """Update or add key=value pairs in the .env file."""
    file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))

    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    found = set()
    for i, line in enumerate(lines):
        for key, value in updates.items():
            if line.startswith(f'{key}='):
                lines[i] = f'{key}={value}\n'
                found.add(key)

    for key, value in updates.items():
        if key not in found:
            lines.append(f'{key}={value}\n')

    with open(file_path, 'w') as f:
        f.writelines(lines)


def handle_setup(args) -> int:
    # Step 1: Supabase credentials
    print("=== Step 1: Supabase credentials ===")
    supa_url = input("Supabase URL: ").strip()
    supa_key = input("Supabase Key: ").strip()

    _update_env({"SUPABASE_URL": supa_url, "SUPABASE_KEY": supa_key})
    print("Saved Supabase credentials to .env\n")

    # Connect to Supabase with the just-entered credentials
    try:
        supa_client = SupabaseClient(supa_url, supa_key)
        db = supa_client.get_client()
    except Exception as e:
        print(f"Failed to connect to Supabase: {e}", file=sys.stderr, flush=True)
        return 1

    print("Supabase connection OK\n")

    # Step 2: Tenant + Wati credentials
    print("=== Step 2: Tenant & Wati credentials ===")
    tenant_name = input("Tenant name: ").strip()
    tenant_slug = input("Tenant slug (url-safe identifier): ").strip()
    tenant_id_input = input("Tenant ID: ").strip()
    default_wati_api_url = f"https://live-mt-server.wati.io/{tenant_id_input}"
    wati_api_url = input(f"Wati API Endpoint URL [{default_wati_api_url}]: ").strip() or default_wati_api_url
    wati_api_token = input("Wati API Token: ").strip()
    default_phone_region = input("Default phone region (ISO alpha-2, e.g. MX, US) [optional]: ").strip().upper() or None
    if default_phone_region and (len(default_phone_region) != 2 or not default_phone_region.isalpha()):
        print("Invalid region code. Must be a 2-letter ISO alpha-2 code (e.g. MX, US, GB).", file=sys.stderr)
        return 1

    try:
        tenant_result = (
            db.table("tenant")
            .insert({"name": tenant_name, "slug": tenant_slug, "tenant_id": tenant_id_input})
            .execute()
        )
        tenant_id = tenant_result.data[0]["tenant_id"]
        print(f"Tenant created: {tenant_name} (ID {tenant_id})")
    except Exception as e:
        print(f"Failed to create tenant: {e}", file=sys.stderr, flush=True)
        return 1

    connector_row = {
        "tenant_id": tenant_id,
        "provider": "wati",
        "wati_api_url": wati_api_url,
        "wati_api_token": wati_api_token,
        "is_active": True,
    }
    if default_phone_region:
        connector_row["default_phone_region"] = default_phone_region

    try:
        db.table("tenant_connectors").insert(connector_row).execute()
        print("Saved Wati connector to Supabase")
    except Exception as e:
        print(f"Failed to save connector: {e}", file=sys.stderr, flush=True)
        return 1

    _update_env({
        "WATI_ACCOUNT_ID": tenant_id_input,
    })
    print("Saved Wati tenant ID to .env\n")

    print("=== Setup complete ===")
    print("Your wati url was created automatically based on your tenant id.")
    print("Wati data was inserted into the database.")
    print(f"Use --tenant-id {tenant_id} when running sync commands.")
    return 0


def handle_sync_contacts(args: argparse.Namespace) -> int:
    try:
        db_client = get_supabase_client()
        wati = get_wati_client(args.tenant_id)
        importer = BatchContactImport(db_client, wati, args.tenant_id)
        result = importer.run(limit=args.limit)
        print(f"Done. total={result['total']} staged={result['upserted_staging']} canonical={result['upserted_canonical']}", flush=True)
        return 0
    except Exception as exc:
        msg = str(exc)
        if "PGRST002" in msg or "schema cache" in msg:
            print("Sync failed: Supabase schema not exposed.", file=sys.stderr, flush=True)
            print(msg)
        else:
            print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1


def handle_repair_contacts(args: argparse.Namespace) -> int:
    job_id = None
    client = None
    try:
        db_client = get_supabase_client()
        repairer = ContactIdentityRepair(db_client, args.tenant_id)
        client = db_client.get_client()
        if not args.dry_run:
            job_result = (
                client.schema("ops").table("import_jobs").insert(
                    {
                        "tenant_id": args.tenant_id,
                        "job_type": "contact_identity_repair",
                        "status": "running",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).execute()
            )
            job_id = job_result.data[0]["job_id"]
            print(f"Repair job created: {job_id}", flush=True)

        result = repairer.run(dry_run=args.dry_run)

        if job_id is not None:
            client.schema("ops").table("import_jobs").update(
                {
                    "status": "completed",
                    "processed_count": result["contacts_examined"],
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("job_id", job_id).execute()

        print(
            "Repair complete. "
            f"examined={result['contacts_examined']} "
            f"contact_updates={result['contact_updates']} "
            f"provider_key_updates={result['provider_key_updates']} "
            f"merge_groups={result['merge_groups']} "
            f"merged_contacts={result['merged_contacts']} "
            f"conflicts={result['conflicts']} "
            f"dry_run={result['dry_run']}",
            flush=True,
        )
        return 0
    except Exception as exc:
        if job_id is not None and client is not None:
            client.schema("ops").table("import_jobs").update(
                {
                    "status": "failed",
                    "error_message": str(exc),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("job_id", job_id).execute()
        print(f"Repair failed: {exc}", file=sys.stderr, flush=True)
        return 1


def _check_existing_backfill(tenant_id: int) -> dict | None:
    """Return the running job dict if a backfill is already active for this tenant, else None."""
    db_client = get_supabase_client()
    result = (
        db_client.get_client().schema("ops").table("import_jobs")
        .select("job_id, subprocess_pid")
        .eq("tenant_id", tenant_id)
        .eq("job_type", "message_backfill")
        .eq("status", "running")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None

    job = rows[0]
    pid = job.get("subprocess_pid")
    if not pid:
        return None

    try:
        os.kill(pid, 0)
        return job
    except OSError:
        # Process is dead — mark the stale job as failed
        db_client.get_client().schema("ops").table("import_jobs").update({
            "status": "failed",
            "error_message": f"Process died unexpectedly (PID {pid} no longer running)",
        }).eq("job_id", job["job_id"]).execute()
        return None


def handle_backfill_messages(args: argparse.Namespace) -> int:
    resume_job_id = getattr(args, 'resume', None)

    if getattr(args, '_background', False):
        # Child process — run synchronously
        try:
            db_client = get_supabase_client()
            wati = get_wati_client(args.tenant_id)
            importer = BatchMessageImport(db_client, wati, args.tenant_id)
            result = importer.run(limit=args.limit, resume_job_id=resume_job_id)
            print(f"Done. total={result['total']} staged={result['upserted_staging']} canonical={result['upserted_canonical']} skipped={result['skipped']}", flush=True)
            return 0
        except Exception as exc:
            msg = str(exc)
            if "PGRST002" in msg or "schema cache" in msg:
                print("Backfill failed: Supabase schema not exposed.", file=sys.stderr, flush=True)
            else:
                print(f"Error: {exc}", file=sys.stderr, flush=True)
            return 1

    # Parent — skip duplicate check when resuming a known job
    if not resume_job_id:
        existing = _check_existing_backfill(args.tenant_id)
        if existing:
            pid = existing.get("subprocess_pid", "?")
            print(f"Backfill already running for tenant {args.tenant_id} (PID {pid}).", flush=True)
            print(f"Use 'kill {pid}' to stop it, or wait for it to finish.", flush=True)
            return 1

    script_dir = Path(__file__).resolve().parent.parent
    log_dir = script_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"backfill_{args.tenant_id}_{timestamp}.log"

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "backfill",
        "--tenant-id", str(args.tenant_id),
        "--_background",
    ]
    if args.limit is not None:
        cmd.extend(["--limit", str(args.limit)])
    if resume_job_id:
        cmd.extend(["--resume", resume_job_id])

    with open(log_file, "w") as lf:
        proc = subprocess.Popen(
            cmd,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONPATH": str(script_dir / "shared")},
        )

    action = "Resumed" if resume_job_id else "Started"
    print(f"Backfill {action.lower()} in background (PID {proc.pid}).", flush=True)
    print(f"Log: {log_file}", flush=True)
    print(f"Monitor: wati-supabase status --tenant-id {args.tenant_id}", flush=True)
    print(f"Stop:    kill {proc.pid}", flush=True)
    return 0


def handle_to_file_contacts(args: argparse.Namespace) -> int:
    try:
        wati = get_wati_client(args.tenant_id)
        contacts = wati.get_contacts()
    except Exception as exc:
        print(f"Failed to fetch contacts: {exc}", file=sys.stderr, flush=True)
        return 1

    output_path = Path(args.output)
    output_path.write_text(json.dumps(contacts, indent=2))
    print(f"Wrote {len(contacts)} contacts to {output_path}", flush=True)
    return 0


def handle_to_file_messages(args: argparse.Namespace) -> int:
    wati = get_wati_client(args.tenant_id)
    if args.phone:
        try:
            result = wati.get_messages(args.phone)
        except Exception as exc:
            print(f"Failed to fetch messages: {exc}", file=sys.stderr, flush=True)
            return 1

        messages = result.get(args.phone, [])
        output_path = Path(args.output) if args.output else Path(f"messages_{args.phone}.json")
        output_path.write_text(json.dumps(messages, indent=2))
        print(f"Wrote {len(messages)} messages to {output_path}", flush=True)
        return 0

    # --all: fetch contacts first, then messages per contact
    try:
        contacts = wati.get_contacts()
    except Exception as exc:
        print(f"Failed to fetch contacts: {exc}", file=sys.stderr, flush=True)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, contact in enumerate(contacts, 1):
        phone = contact.get("wAid") or contact.get("phone") or contact.get("whatsappNumber")
        if not phone:
            print(f"[{i}/{len(contacts)}] Skipping contact with no phone number", flush=True)
            continue
        print(f"[{i}/{len(contacts)}] Fetching messages for {phone}", flush=True)
        try:
            result = wati.get_messages(phone)
            messages = result.get(phone, [])
        except Exception as exc:
            print(f"  Error fetching messages for {phone}: {exc}", flush=True)
            continue

        file_path = output_dir / f"messages_{phone}.json"
        file_path.write_text(json.dumps(messages, indent=2))

    print(f"Done. Files written to {output_dir}/", flush=True)
    return 0


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def handle_logs(args: argparse.Namespace) -> int:
    log_dir = Path(__file__).resolve().parent.parent / "logs"

    if not log_dir.exists():
        print("No log files found.", flush=True)
        return 0

    if args.file:
        log_path = log_dir / args.file
        if not log_path.exists():
            print(f"Log file not found: {args.file}", file=sys.stderr, flush=True)
            return 1
        content = log_path.read_text()
        if args.tail:
            lines = content.splitlines()
            for line in lines[-50:]:
                print(line)
        else:
            print(content, end="")
        return 0

    log_files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not log_files:
        print("No log files found.", flush=True)
        return 0

    if args.list:
        print("Log files (newest first):", flush=True)
        for f in log_files:
            size = _format_size(f.stat().st_size)
            print(f"  {f.name}  ({size})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        build_parser().print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
