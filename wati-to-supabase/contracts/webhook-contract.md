# Webhook Contract

Real-time ingress path for Wati message events via Supabase Edge Function.

## Endpoint

```
POST https://<project>.supabase.co/functions/v1/wati-webhook?tenant_id=<ID>&secret=<SECRET>
```

- `tenant_id` (required) — numeric tenant ID matching `public.tenant.tenant_id`
- `secret` (optional) — must match `tenant_connectors.webhook_secret` if set

## Authentication

Per-tenant webhook secret stored in `public.tenant_connectors.webhook_secret`.
When the column is NULL, secret validation is skipped (open mode for testing).
When set, the `secret` query parameter must match exactly.

Wati webhook configuration allows setting a custom URL per account — include
the tenant_id and secret in the configured URL.

## Wati Payload Shape

Wati POSTs a JSON body for each message event. Key fields:

| Field | Type | Maps to |
|-------|------|---------|
| `id` | string | `wati_message_id` |
| `waId` | string | `conversation_id` (raw phone, normalized by SQL) |
| `text` / `body` | string | `message_content` |
| `type` | string | `direction` (inbound/outbound) |
| `messageType` | string | message type (text, image, template) |
| `timestamp` | number/string | Unix epoch or ISO 8601 |
| `eventType` | string | event classification (e.g. "messages") |
| `whatsappMessageId` | string | fallback message ID |

## Processing Pipeline

1. Validate tenant and webhook secret
2. Log raw payload to `ops.webhook_logs`
3. Upsert into `staging.messages_raw_data` (idempotent via `tenant_id, wati_message_id`)
4. Promote to `canonical.messages` via `process_webhook_message_to_canonical(staging_id)`
5. Record outcome in `ops.webhook_processing_log`
6. Update `ops.webhook_processing_status` aggregates

## Idempotency

Duplicate deliveries are safe:
- Staging upsert on `(tenant_id, wati_message_id)` — second delivery is a no-op
- Canonical insert uses `ON CONFLICT DO NOTHING`
- Each webhook log entry gets its own processing log record

## Contact Dependency

Message promotion requires a matching canonical contact (joined via
`normalize_phone(conversation_id)`). If no contact exists:

- Message is staged successfully
- Promotion returns status `skipped` with reason `no_matching_contact`
- Processing log records the skip
- The next `sync contacts` + batch promotion picks up orphaned messages

## Health Monitoring

`ops.webhook_processing_status` provides per-tenant aggregates:
- `last_received_at` / `last_processed_at` — recency indicators
- `last_status` — success / skipped / failed
- `total_received` / `total_succeeded` / `total_failed` — lifetime counters

## Response Format

Success (200):
```json
{
  "status": "success",
  "log_id": "uuid",
  "staging_id": "uuid",
  "promotion": { "status": "success", "contact_id": "uuid", ... }
}
```

Skipped (200, contact not found):
```json
{
  "status": "skipped",
  "log_id": "uuid",
  "staging_id": "uuid",
  "promotion": { "status": "skipped", "reason": "no_matching_contact", ... }
}
```

Error (500):
```json
{
  "status": "failed",
  "log_id": "uuid",
  "error": "description"
}
```
