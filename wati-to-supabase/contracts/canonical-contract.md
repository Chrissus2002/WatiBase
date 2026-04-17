# Canonical Contract

Draft connector-owned output contract for `Wati -> Supabase`.

## Purpose

This is the stable product surface the connector should guarantee.

Current physical mapping is still legacy:

- `silver.contacts`
- `silver.contact_wati_keys`
- `silver.messages`

Target conceptual mapping is:

- `canonical.contacts`
- `canonical.contact_provider_keys`
- `canonical.messages`

## Contract Shape

### `canonical.contacts`

Minimum expectations:

- one row per canonical contact identity
- stable `contact_id` and `tenant_id`
- stable identity key suitable for dedupe/grouping
- provider-agnostic contact semantics
- raw/display fields may exist, but do not define canonical identity

### `canonical.contact_provider_keys`

Minimum expectations:

- maps provider identities to canonical contacts
- stores provider contact IDs and provider lookup phones
- provider-specific rows live here rather than defining core contact meaning

### `canonical.messages`

Minimum expectations:

- one row per conversational unit
- stable `message_id`, `tenant_id`, and `contact_id`
- trustworthy chronological `timestamp`
- stable `direction`
- user-meaningful `text`
- provider message/conversation references as reference fields, not canonical
  meaning

## Connector Guarantees

- contacts and messages are queryable by `tenant_id`
- canonical direction semantics are connector-guaranteed
- canonical ordering is connector-guaranteed
- provider-specific keys are available without making provider fields the core
  contact model

## Out Of Scope

- raw webhook/debug payloads
- analytics views
- prompt configuration
