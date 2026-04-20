#!/usr/bin/env python3
"""
CLI-first admin surface for the Wati -> Supabase connector.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from dotenv import load_dotenv
from typing import Any

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'shared'))

from supabase_client import SupabaseClient

@dataclass
class StatusSnapshot:
    tenant_id: int
    tenant_name: str
    tenant_slug: str | None
    wati_api_configured: bool
    wati_phone_number: str | None
    drift_run: dict[str, Any] | None
    active_job: dict[str, Any] | None


def _placeholder(parser: argparse.ArgumentParser, command_path: str) -> None:
    parser.set_defaults(handler=handle_not_implemented, command_path=command_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wati-supabase",
        description="CLI admin surface for the Wati -> Supabase connector",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Connector install and setup helpers")
    setup_parser.add_argument("--wati", action='store_true', help="Setting up wati credentials")
    setup_parser.add_argument("--supabase", action='store_true', help="Setting up supabase credentials")
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
    _placeholder(sync_contacts_parser, "sync contacts")

    backfill_parser = subparsers.add_parser("backfill", help="Run connector backfill operations")
    backfill_subparsers = backfill_parser.add_subparsers(dest="backfill_command", required=True)
    backfill_messages_parser = backfill_subparsers.add_parser("messages", help="Backfill historical messages")
    _placeholder(backfill_messages_parser, "backfill messages")

    drift_parser = subparsers.add_parser("drift", help="Exact drift and integrity checks")
    drift_subparsers = drift_parser.add_subparsers(dest="drift_command", required=True)
    drift_check_parser = drift_subparsers.add_parser("check", help="Run an exact drift check")
    _placeholder(drift_check_parser, "drift check")

    logs_parser = subparsers.add_parser("logs", help="Inspect connector logs")
    _placeholder(logs_parser, "logs")

    doctor_parser = subparsers.add_parser("doctor", help="Run connector health checks")
    doctor_parser.set_defaults(handler=handle_test_connection)

    daemon_parser = subparsers.add_parser("daemon", help="Control the connector daemon")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_run_parser = daemon_subparsers.add_parser("run", help="Run the daemon loop")
    _placeholder(daemon_run_parser, "daemon run")

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def handle_not_implemented(args: argparse.Namespace) -> int:
    print(f"{args.command_path} is scaffolded but not implemented yet.", file=sys.stderr, flush=True)
    return 2


def handle_test_connection(args: argparse.Namespace) -> int:
    try:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")
        client = SupabaseClient(supabase_url, supabase_key)
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
    client = get_cli_supabase_client()

    tenant_response = (
        client.table("tenants")
        .select("tenant_id, name, slug, wati_api_token, wati_phone_number")
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    tenant_rows = tenant_response.data or []
    if not tenant_rows:
        raise ValueError(f"Tenant {tenant_id} not found")

    tenant = tenant_rows[0]

    drift_response = (
        client.table("daemon_task_runs")
        .select("status, started_at, finished_at, result, error_message, notes")
        .eq("tenant_id", tenant_id)
        .eq("task_name", "exact_contact_drift")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    drift_rows = drift_response.data or []

    active_job_response = (
        client.table("import_jobs")
        .select(
            "job_id, status, created_at, completed_at, processed_messages, "
            "total_messages, subprocess_pid"
        )
        .eq("tenant_id", tenant_id)
        .in_("status", ["pending", "running"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    active_job_rows = active_job_response.data or []

    return StatusSnapshot(
        tenant_id=tenant["tenant_id"],
        tenant_name=tenant.get("name") or f"Tenant {tenant_id}",
        tenant_slug=tenant.get("slug"),
        wati_api_configured=bool(tenant.get("wati_api_token")),
        wati_phone_number=tenant.get("wati_phone_number"),
        drift_run=drift_rows[0] if drift_rows else None,
        active_job=active_job_rows[0] if active_job_rows else None,
    )


def format_status(snapshot: StatusSnapshot) -> str:
    lines: list[str] = []
    lines.append(f"Tenant: {snapshot.tenant_name} ({snapshot.tenant_id})")
    lines.append(f"Slug: {snapshot.tenant_slug or '-'}")
    lines.append(f"Wati token: {'configured' if snapshot.wati_api_configured else 'missing'}")
    lines.append(f"Business phone: {snapshot.wati_phone_number or '-'}")
    lines.append("")

    if snapshot.drift_run is None:
        lines.append("Exact drift: not run yet")
    else:
        drift = snapshot.drift_run
        result = drift.get("result") or {}
        lines.append(
            f"Exact drift: {drift.get('status', 'unknown')}"
            f" (started {drift.get('started_at') or '-'})"
        )
        if result:
            lines.append(f"  Wati distinct: {result.get('wati_distinct_contact_count', 0):,}")
            lines.append(f"  DB: {result.get('db_contact_count', 0):,}")
            lines.append(f"  Wati-only: {result.get('wati_only_count', 0):,}")
            lines.append(f"  DB-only: {result.get('db_only_count', 0):,}")
            lines.append(f"  Exact match: {str(result.get('exact_match', False)).lower()}")
        elif drift.get("error_message"):
            lines.append(f"  Error: {drift['error_message']}")
        elif drift.get("notes"):
            lines.append(f"  {drift['notes']}")

    lines.append("")
    if snapshot.active_job is None:
        lines.append("Active import job: none")
    else:
        job = snapshot.active_job
        lines.append(
            f"Active import job: {job.get('status', 'unknown')}"
            f" ({job.get('processed_messages', 0):,}/{job.get('total_messages', 0):,})"
        )
        lines.append(f"Job ID: {job.get('job_id')}")
        lines.append(f"Started: {job.get('created_at') or '-'}")
        lines.append(f"PID: {job.get('subprocess_pid') or '-'}")

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
    
def handle_setup(args) -> int:
    if args.wati:
        setup_wati_credentials()
    if args.supabase:
        setup_supabase_credentials()
    return 0

def setup_wati_credentials() -> bool:
    wati_account_id = input("Wati Account ID: ")
    wati_api_token = input("Wati API Token: ")
    file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    
    # Read existing .env file
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # Update or add WATI_ACCOUNT_ID and WATI_API_TOKEN
    wati_id_updated = False
    wati_token_updated = False
    for i, line in enumerate(lines):
        if line.startswith('WATI_ACCOUNT_ID='):
            lines[i] = f'WATI_ACCOUNT_ID={wati_account_id}\n'
            wati_id_updated = True
        elif line.startswith('WATI_API_TOKEN='):
            lines[i] = f'WATI_API_TOKEN={wati_api_token}\n'
            wati_token_updated = True
    
    # If not found, add them
    if not wati_id_updated:
        lines.append(f'WATI_ACCOUNT_ID={wati_account_id}\n')
    if not wati_token_updated:
        lines.append(f'WATI_API_TOKEN={wati_api_token}\n')
    
    # Write back to .env file
    with open(file_path, 'w') as f:
        f.writelines(lines)
    
    print("Updated Wati credentials")
    return True

def setup_supabase_credentials() -> bool:
    supabase_url = input("Supabase URL: ")
    supabase_anon_key = input("Supabase Anon Key: ")
    file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    
    # Read existing .env file
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # Update or add SUPABASE_URL and SUPABASE_ANON_KEY
    supabase_url_updated = False
    supabase_anon_key_updated = False
    for i, line in enumerate(lines):
        if line.startswith('SUPABASE_URL='):
            lines[i] = f'SUPABASE_URL={supabase_url}\n'
            supabase_url_updated = True
        elif line.startswith('SUPABASE_KEY='):
            lines[i] = f'SUPABASE_KEY={supabase_anon_key}\n'
            supabase_anon_key_updated = True
    
    # If not found, add them
    if not supabase_url_updated:
        lines.append(f'SUPABASE_URL={supabase_url}\n')
    if not supabase_anon_key_updated:
        lines.append(f'SUPABASE_KEY={supabase_anon_key}\n')
    
    # Write back to .env file
    with open(file_path, 'w') as f:
        f.writelines(lines)
    
    print("Updated Supabase credentials")
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        build_parser().print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
