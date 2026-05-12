# Supabase Edge Function for Wati Webhooks

A Supabase Edge Function receives live message events from Wati and feeds them into the same staging-to-canonical pipeline.

**Endpoint:**

```
POST https://<project>.supabase.co/functions/v1/wati-webhook?tenant_id=<ID>&secret=<SECRET>
```

- `tenant_id` (required) -- must match a `public.tenant.tenant_id`
- `secret` (optional) -- must match `tenant_connectors.webhook_secret` if set. When the column is NULL, secret validation is skipped (open mode for testing).

**Setup:**

1. Deploy the edge function: copy the contents of `supabase/functions/wati-webhook/index.ts` into a new edge function in the Supabase dashboard
2. In your Wati account, set the webhook URL to the endpoint above (include your `tenant_id` and `secret` in the URL)

**What happens on each webhook:**

1. Validates tenant and webhook secret
2. Logs the raw payload to `ops.webhook_logs`
3. Upserts the message into `staging.messages_raw_data`
4. Promotes to `canonical.messages` via a database function
5. Records the processing outcome in `ops.webhook_processing_log`
6. Updates health counters in `ops.webhook_processing_status`

Duplicate deliveries are safe -- staging upserts on `(tenant_id, wati_message_id)` and canonical inserts use `ON CONFLICT DO NOTHING`.

If a message arrives from an unknown phone number, a minimal contact is auto-created (with `source = 'webhook'`). The next `sync contacts` run will enrich it with the full Wati profile.