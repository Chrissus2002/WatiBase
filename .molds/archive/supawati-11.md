<!-- molds
id: supawati-11
stage: closed
resume_stage: 
parent: 
deps: 
priority: P2
created_at: 2026-04-29T17:52:16Z
updated_at: 2026-04-29T18:13:28Z
closed_at: 2026-04-29T18:13:28Z
defer_until: 
code_branch: 
-->




# Wati webhook edge function for real-time message ingestion

## TL;DR
Supabase Edge Function (Deno/TypeScript) that receives Wati webhook POSTs in real-time, lands raw message payloads in staging, promotes to canonical via existing SQL RPCs, and tracks webhook health in the ops tables. Gives SupaWati live message ingestion alongside the existing CLI batch backfill. Includes a new SQL RPC for single-message promotion since the existing `process_messages_to_canonical` is batch-oriented.

## Problem
Today messages only enter the system via CLI-driven batch backfill (`./wati-supabase backfill`). There is no real-time path — messages are stale until the next manual import run. The database already has webhook infrastructure tables (`ops.webhook_logs`, `ops.webhook_processing_log`, `ops.webhook_processing_status`) and the staging→canonical promotion RPCs, but no handler code exists to receive and process incoming Wati events.

## Solution

### Edge Function: `supabase/functions/wati-webhook/index.ts`

A Deno Edge Function that handles the full webhook lifecycle:

1. **Receive** — Accept POST from Wati with message event payload
2. **Authenticate** — Validate request via a shared webhook secret (per-tenant, stored in `tenant_connectors` or passed as query param `?tenant_id=X&secret=Y`)
3. **Log** — Insert raw payload into `ops.webhook_logs` (event_type extracted from payload)
4. **Stage** — Upsert message into `staging.messages_raw_data` with a webhook-specific batch reference
5. **Promote** — Call a new `process_single_message_to_canonical()` RPC (or reuse batch RPC with a single-row batch)
6. **Record** — Write outcome to `ops.webhook_processing_log` and update `ops.webhook_processing_status`
7. **Respond** — Return 200 with processing summary (or 4xx/5xx on error)

### Tenant Resolution Strategy

Wati webhooks are configured per-account with a custom URL. Use the Edge Function URL with tenant identification in the path or query:
```
https://<project>.supabase.co/functions/v1/wati-webhook?tenant_id=310131
```
The function validates the tenant exists and has an active connector before processing.

### Wati Webhook Payload Shape

Wati sends webhook events for new messages. Key fields in the payload:
- `id` — Wati message ID (maps to `wati_message_id`)
- `waId` — WhatsApp ID / phone of the contact (maps to `conversation_id`)
- `text` / `body` — message content
- `type` — message type (text, image, etc.)
- `timestamp` — Unix epoch or ISO string
- `eventType` — "messages" for incoming messages
- `whatsappMessageId` — WhatsApp-level message ID

### Key Design Decisions

1. **Single-message RPC vs batch RPC**: The existing `process_messages_to_canonical(batch_id)` expects a batch of messages. For webhook single-message flow, add a lightweight `process_webhook_message_to_canonical(p_staging_id)` that promotes one row by its staging ID — avoids creating throwaway import jobs for every webhook.

2. **Contact must exist**: Message promotion joins on `canonical.contacts` via `normalize_phone(conversation_id)`. If a message arrives for an unknown contact, stage it but mark it as unresolvable in `ops.webhook_processing_log` (status='skipped'). The next `sync contacts` run will create the contact, and a follow-up batch promotion can pick up orphaned staged messages.

3. **Idempotency**: `staging.messages_raw_data` has a unique constraint on `(tenant_id, wati_message_id)`. Duplicate webhooks are handled via upsert — safe to receive the same event multiple times.

4. **No import_jobs for webhooks**: Webhook messages set `batch_id = NULL` in staging. The webhook_logs/processing_log tables handle webhook-specific tracking. This keeps the import_jobs table clean for CLI batch operations.

5. **Auth**: Use a webhook secret per tenant (new column `webhook_secret` on `tenant_connectors`) validated in the edge function. Wati allows setting custom headers on webhook config.

## Files to Modify

| File | Action | Purpose | Done |
|------|--------|---------|------|
| `supabase/functions/wati-webhook/index.ts` | Create | Edge Function: receive, validate, stage, promote, log | yes |
| `database/functions.sql` | Modify | Add `process_webhook_message_to_canonical(p_staging_id)` RPC | yes |
| `database/init_db.sql` | Modify | Add `webhook_secret` column to `tenant_connectors` | yes |
| `supabase/functions/wati-webhook/deno.json` | Create | Deno config with import map for supabase-js | yes |
| `wati-to-supabase/contracts/webhook-contract.md` | Create | Document webhook payload shape, auth, and processing guarantees | yes |

## Verification

1. **Unit**: Deploy edge function locally via `supabase functions serve wati-webhook` and POST a sample Wati payload — verify 200 response and rows in webhook_logs + staging
2. **Integration**: Send a message in Wati to a known contact → verify it appears in `staging.messages_raw_data` and `canonical.messages` within seconds
3. **Unknown contact**: Send message for a phone not in canonical.contacts → verify it stages but logs as 'skipped', then run `sync contacts` and verify the message promotes on next webhook or batch
4. **Duplicate delivery**: POST the same payload twice → verify only one staging row, second returns 200 with no error
5. **Auth failure**: POST without valid secret → verify 401 response, no rows written
6. **Health tracking**: After several webhooks, verify `ops.webhook_processing_status` shows correct counts for the tenant

## Implementation Notes

- Supabase Edge Functions run Deno (TypeScript). Use `@supabase/supabase-js` for DB access with the `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` env vars (auto-injected in deployed functions).
- `staging.messages_raw_data.batch_id` currently has a FK to `ops.import_jobs(job_id)` — needs to become nullable (or we create a sentinel webhook job per tenant). Nullable is cleaner.
- The `conversation_id` for webhook messages should be `normalize_phone(waId)` from the Wati payload to match the batch import pattern where `conversation_id` = the phone used for lookup.
- Mexican mobile number normalization (`52X → 521X`) must be applied consistently — use the SQL `normalize_phone()` function via the RPC rather than reimplementing in TypeScript.
- Consider rate limiting in future if webhook volume becomes high, but for MVP the single-message-per-request pattern is fine.
