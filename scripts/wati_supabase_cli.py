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
from dataclasses import dataclass
from dotenv import load_dotenv
from pathlib import Path
from typing import Any

from supabase_client import SupabaseClient
from wati_client import WatiClient
from batch_contact_import import BatchContactImport
from batch_message_import import BatchMessageImport

load_dotenv()
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
try:
    client = SupabaseClient(supabase_url, supabase_key)
except Exception as e:
    print(f"Error initializing Supabase client: {e}")
    sys.exit(1)
def get_wati_client(tenant_id: int) -> WatiClient:
    """Create a WatiClient from DB credentials for the given tenant."""
    return WatiClient.from_supabase(client, tenant_id)

@dataclass
class StatusSnapshot:
    supabase_url: str
    supabase_key: str
    tenant_id: int
    tenant_name: str
    tenant_slug: str | None
    wati_api_url: str | None
    wati_api_token: str | None


def _placeholder(parser: argparse.ArgumentParser, command_path: str) -> None:
    parser.set_defaults(handler=handle_not_implemented, command_path=command_path)


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

    status_parser = subparsers.add_parser("status", help="Show connector status for a tenant")
    status_parser.add_argument("--tenant-id", type=int, required=True, help="Tenant ID")
    status_parser.set_defaults(handler=handle_status)

    watch_parser = subparsers.add_parser("watch", help="Poll selected status surfaces")
    watch_subparsers = watch_parser.add_subparsers(dest="watch_command", required=True)
    watch_status_parser = watch_subparsers.add_parser("status", help="Poll connector status")
    watch_status_parser.add_argument("--tenant-id", type=int, required=True, help="Tenant ID")
    watch_status_parser.add_argument("--interval", type=int, default=10, help="Refresh interval in seconds")
    watch_status_parser.set_defaults(handler=handle_watch_status)

    sync_parser = subparsers.add_parser("sync", help="Run connector sync operations")
    sync_subparsers = sync_parser.add_subparsers(dest="sync_command", required=True)
    sync_contacts_parser = sync_subparsers.add_parser("contacts", help="Sync contacts from Wati")
    sync_contacts_parser.add_argument("--tenant-id", type=int, required=True, help="DB tenant ID")
    sync_contacts_parser.add_argument("--limit", type=int, default=None, help="Max number of contacts to import (for testing)")
    sync_contacts_parser.set_defaults(handler=handle_sync_contacts)

    backfill_parser = subparsers.add_parser("backfill", help="Backfill historical messages")
    backfill_parser.add_argument("--tenant-id", type=int, required=True, help="DB tenant ID")
    backfill_parser.add_argument("--limit", type=int, default=None, help="Max contacts to process (for testing)")
    backfill_parser.add_argument("--resume", type=str, default=None, help="Resume a failed/interrupted job by job_id")
    backfill_parser.add_argument("--_background", action="store_true", default=False, help=argparse.SUPPRESS)
    backfill_parser.set_defaults(handler=handle_backfill_messages)

    logs_parser = subparsers.add_parser("logs", help="Inspect connector logs")
    _placeholder(logs_parser, "logs")

    doctor_parser = subparsers.add_parser("doctor", help="Run connector health checks")
    doctor_parser.set_defaults(handler=handle_test_connection)

    daemon_parser = subparsers.add_parser("daemon", help="Control the connector daemon")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_run_parser = daemon_subparsers.add_parser("run", help="Run the daemon loop")
    _placeholder(daemon_run_parser, "daemon run")

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


def handle_not_implemented(args: argparse.Namespace) -> int:
    print(f"{args.command_path} is scaffolded but not implemented yet.", file=sys.stderr, flush=True)
    return 2


def handle_test_connection(args: argparse.Namespace) -> int:
    try:
        response = client.test_connection()

        if response:
            print("Supabase connection OK", flush=True)
            return 0
        else:
            print("Supabase connection failed", flush=True)
            return 1
    except Exception as e:
        print(f"Connection error: {e}", flush=True)
        return 1


def load_status_snapshot(tenant_id: int) -> StatusSnapshot:
    tenant_response = (
        client.get_client().table("tenant")
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
        client.get_client().table("tenant_connectors")
        .select("wati_api_url, wati_api_token")
        .eq("tenant_id", tenant_id)
        .eq("provider", "wati")
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    connector_rows = connector_response.data or []
    connector = connector_rows[0] if connector_rows else {}

    return StatusSnapshot(
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        tenant_id=tenant["tenant_id"],
        tenant_name=tenant.get("name") or f"Tenant {tenant_id}",
        tenant_slug=tenant.get("slug"),
        wati_api_url=connector.get("wati_api_url"),
        wati_api_token=connector.get("wati_api_token"),
    )


def format_status(snapshot: StatusSnapshot) -> str:
    lines: list[str] = []
    lines.append("Supabase")
    lines.append(f"  URL:   {snapshot.supabase_url}")
    lines.append(f"  Key:   {snapshot.supabase_key}")
    lines.append("")
    lines.append("Tenant")
    lines.append(f"  ID:    {snapshot.tenant_id}")
    lines.append(f"  Name:  {snapshot.tenant_name}")
    lines.append(f"  Slug:  {snapshot.tenant_slug or '-'}")
    lines.append("")
    lines.append("Wati Connector")
    lines.append(f"  URL:   {snapshot.wati_api_url or 'not configured'}")
    lines.append(f"  Token: {snapshot.wati_api_token or 'not configured'}")

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
    wati_api_url = "https://live-server.wati.io/" + tenant_id_input
    wati_api_token = input("Wati API Token: ").strip()

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

    try:
        db.table("tenant_connectors").insert({
            "tenant_id": tenant_id,
            "provider": "wati",
            "wati_api_url": wati_api_url,
            "wati_api_token": wati_api_token,
            "is_active": True,
        }).execute()
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
        wati = get_wati_client(args.tenant_id)
        importer = BatchContactImport(client, wati, args.tenant_id)
        result = importer.run(limit=args.limit)
        print(f"Done. total={result['total']} staged={result['upserted_staging']} canonical={result['upserted_canonical']}", flush=True)
        return 0
    except Exception as exc:
        msg = str(exc)
        if "PGRST002" in msg or "schema cache" in msg:
            print("Sync failed: Supabase schema not exposed.", file=sys.stderr, flush=True)
        else:
            print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1


def _check_existing_backfill(tenant_id: int) -> dict | None:
    """Return the running job dict if a backfill is already active for this tenant, else None."""
    result = (
        client.get_client().schema("ops").table("import_jobs")
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
        client.get_client().schema("ops").table("import_jobs").update({
            "status": "failed",
            "error_message": f"Process died unexpectedly (PID {pid} no longer running)",
        }).eq("job_id", job["job_id"]).execute()
        return None


def handle_backfill_messages(args: argparse.Namespace) -> int:
    resume_job_id = getattr(args, 'resume', None)

    if getattr(args, '_background', False):
        # Child process — run synchronously
        try:
            wati = get_wati_client(args.tenant_id)
            importer = BatchMessageImport(client, wati, args.tenant_id)
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        build_parser().print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
