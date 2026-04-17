# Tenant Connectors Model

Draft ownership split for workspace vs connector installation data.

## Goal

Keep one shared `tenant_id`, but stop storing Wati installation details directly
on the workspace row.

## Target Shape

### `public.tenants`

Owns workspace/account identity:

- `tenant_id`
- workspace metadata
- product-neutral account fields

### `public.tenant_connectors`

Owns connector installation state:

- `connector_id`
- `tenant_id`
- `provider`
- provider workspace/account identifier
- API token or secret reference
- provider phone number(s)
- webhook status
- `default_phone_region`
- sync/health timestamps and last error

## Rules

- one tenant may have zero or more connectors over time
- in the Wati product phase, only one active Wati install should exist per
  tenant
- `default_phone_region` belongs on the connector install, not the workspace

## Why This Matters

This keeps the tenant model shared across products while keeping provider
credentials and runtime state in the connector-owned boundary.
