# Phase A Boundary Cleanup

Working notes for `dialo-6`.

## Scope

Phase A is architecture cleanup, not physical schema rename.

Focus areas:

- connector ownership and docs
- tenant vs connector-installation split
- `ops` schema introduction
- neutral shared runtime extraction
- explicit canonical contract
- CLI-first admin surface (`wati-supabase`) instead of a dedicated connector UI

## Current Legacy Homes

- `scripts/`
- `supabase/functions/wati-webhook/`
- `database/`
- `app/database.py`

## First Expected Outputs

- scaffolded contract docs under `wati-to-supabase/contracts/`
- neutral runtime start under `shared/runtime/`
- clearer connector-facing notes before any large code move

## Explicit Non-Goal

Do not physically rename `bronze/silver/gold` in this phase. That belongs to
`dialo-8`.
