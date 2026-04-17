# Staging Semantics

Draft first-principles contract for connector ingress.

## Purpose

`staging` is connector-owned ingress, but not every raw source behaves the same
way. Messages and contacts must use different semantics.

Current physical mapping is still legacy:

- `bronze.wati_messages`
- `bronze.wati_contacts`

## Message Ingress

Treat messages as an event stream.

Minimum expectations:

- one logical row per provider message/event identity
- duplicate deliveries handled idempotently
- first-seen raw payload remains the canonical ingress record
- payloads are immutable once captured, except for explicit operational repair

## Contact Ingress

Treat contacts as latest-seen provider snapshots.

Minimum expectations:

- upsert current state per provider contact identity
- track import run and `last_seen_at`
- do not pretend the latest-state table is append-only history

If historical snapshots are needed later, add explicit history rather than
overloading the main latest-state table.

## Operational Implication

- webhook and batch flows may both write to staging
- canonical promotion must understand the event-vs-snapshot distinction
- drift checks validate connector health, but are not part of staging semantics
