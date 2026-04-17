# Wati to Supabase

Standalone product home for the `Wati -> Supabase` connector.

## Purpose

This product owns the Wati-specific ingestion and sync pipeline:

- Wati API client
- batch contact imports
- batch message backfills
- webhook ingestion
- phone identity reconciliation needed for Wati lookups
- drift checks and daemonized operational health

Its job is to write a clean conversation dataset into Supabase.

## Target Layer Model

This product should be described using explicit layer names rather than
medallion jargon:

- `staging`: raw provider landing area
- `canonical`: normalized contact/message contract shared with downstream apps
- `analytics`: downstream reporting projections
- `ops`: jobs, webhook logs, drift runs, and operational state

For the connector product, the center of gravity is `staging` + `canonical` +
`ops`.

## Current Code That Belongs Here

- `scripts/wati_client.py`
- `scripts/batch_contact_imports.py`
- `scripts/batch_messages_import.py`
- `scripts/phone_identity.py`
- `scripts/task_daemon.py`
- `scripts/daemon_tasks/`
- `supabase/functions/wati-webhook/`
- Wati-focused schema and processing logic in `database/`

## Does Not Own

- analytics dashboards
- application-layer UX outside the connector CLI

## Near-Term Goal

Turn the current Dialo ingestion subsystem into a clearly standalone connector
with its own docs, runtime boundary, and Supabase contract, while keeping it in
this repo for now.

## Scaffold

- `docs/` — connector-facing docs and operator guidance
- `contracts/` — connector-owned contracts and schema-facing notes
- `runtime/` — runtime boundary and extraction notes
- `cli/` — connector CLI and admin UX notes

## Seed Files

- `contracts/canonical-contract.md`
- `contracts/staging-semantics.md`
- `contracts/tenant-connectors-model.md`
- `runtime/phase-a-boundary-cleanup.md`
- `runtime/phase-b-rename-cutover.md`
- `cli/README.md`
