<!-- molds
id: supawati-10
stage: design
resume_stage:
parent:
deps:
priority: P1
created_at: 2026-04-24T07:46:02Z
updated_at: 2026-04-24T07:50:00Z
closed_at:
defer_until:
code_branch:
-->

# Port dialo-cx phone identity and smart contact deduplication

## TL;DR

- Wati-Base currently deduplicates contacts with a simple digits-only key
  (`regexp_replace(phone, '\D', '', 'g')`)
- Port the connector-owned phone identity model from `dialo-cx` so Wati-Base
  can:
  - preserve the legacy Wati-compatible lookup semantics (`52... -> 521...`)
  - store richer canonical identity fields (`phone_identity_key`, `phone_e164`,
    normalization status/region, `wati_lookup_phone`)
  - use connector-level `default_phone_region` when parsing national-format
    numbers
  - fetch messages via the provider lookup phone instead of whatever raw phone
    happened to win the latest upsert
- Do **not** port app/UI/classifier logic. Port only the connector-owned identity
  logic and its tests/fixtures.

## Problem

Wati-Base does not yet have the same phone identity behavior as `dialo-cx`.

Today the connector uses:

- `public.normalize_phone()` in `database/functions.sql`:
  - strips non-digits only
- `canonical.contacts.phone_normalized` as the sole dedupe key
- `canonical.contacts.phone_raw` as the phone later used to fetch messages

This is materially weaker than the current `dialo-cx` model:

1. Mexican mobile numbers can be represented by Wati as either `52...` or
   `521...`; the current Wati-Base normalizer treats these as different contacts
2. National-format numbers cannot be parsed with a tenant/connector-specific
   default region
3. Wati-Base has no shadow identity key, so cross-format matches are weaker
4. Wati-Base has no explicit `wati_lookup_phone`, so message fetches depend on
   whichever raw phone happened to be stored
5. There is no structured fallback story for ambiguous numbers (`wati:<id>`,
   `raw:<digits>`, etc.)

The result is that Wati-Base can appear to work on clean data while still
splitting one person into multiple contacts or failing to match the exact phone
representation Wati expects for `/getMessages`.

## Source of Truth To Port From dialo-cx

Port the connector-owned pieces from these files:

| Source file | What to port |
|-------------|--------------|
| `database/05_functions.sql` | legacy Wati-compatible `normalize_phone` behavior, especially MX mobile `52 -> 521` |
| `scripts/phone_identity.py` | `phonenumbers`-based parser, identity/fallback model, `legacy_wati_lookup_phone()`, `build_phone_identity()` |
| `database/03_silver_layer.sql` | contact identity fields and indexes that belong in connector-owned canonical data |
| `tests/unit/test_phone_identity.py` | identity behavior tests |
| `tests/fixtures/phone_identity_cases.json` | cross-format / multi-country fixture set |
| archived design notes in `.molds/archive/dialo-6.md` | placement of `default_phone_region` on connector install, plus the rule that reconciliation is connector-owned enrichment, not app logic |

Do **not** blindly copy schema names (`silver`, `gold`) or app-layer helpers.
Adapt the connector-owned semantics into Wati-Base's `public` / `canonical` /
`ops` layout.

## Chosen Implementation Shape

Port the hardened normalization from `dialo-cx`, but do it in a way that fits
Wati-Base's current architecture instead of trying to recreate `dialo-cx`
verbatim.

Locked design:

1. Keep SQL as the authority for the **legacy Wati-compatible lookup key**
   needed by constraints and message joins
2. Port the `phonenumbers` parser into Python as a neutral
   `shared/phone_identity.py` module
3. Keep `process_contacts_to_canonical()` as the fast SQL path that creates
   usable canonical rows immediately
4. Add a synchronous Python enrichment pass at the end of
   `BatchContactImport.run()` that fills the richer identity fields on those
   canonical rows
5. Make message backfill read `wati_lookup_phone` first, with `phone_raw` only
   as a transitional fallback during rollout

This is the right shape because:

- `phonenumbers` already exists and is hardened in Python, not SQL
- Wati-Base already has a working staging -> SQL promotion pipeline
- we do not want canonical row creation to fail just because parsing is
  ambiguous
- we do want the hardened `dialo-cx` identity semantics to become the source of
  truth for the connector

## Exactly What To Port

### 1. Legacy Wati lookup semantics

Port the legacy Wati-compatible lookup rule from `dialo-cx`:

- strip non-digits
- if the number starts with `52` but not `521`, rewrite it to `521...`

This must become Wati-Base's compatibility normalizer for:

- `canonical.contacts.phone_normalized`
- `canonical.contacts.wati_lookup_phone`
- joins from message staging to canonical contacts
- any CLI/API path that fetches messages from Wati

Concrete requirement:

- `public.normalize_phone()` in Wati-Base must stop being digits-only and match
  the `dialo-cx` legacy helper exactly for the lookup semantics that matter to
  Wati
- the Python `legacy_wati_lookup_phone()` helper must mirror that same behavior
  so SQL joins and Python fetches cannot drift apart

### 2. Shared phone identity library

Port `dialo-cx/scripts/phone_identity.py` into a connector-neutral Wati-Base
module, likely `shared/phone_identity.py`, with at least:

- `strip_to_digits()`
- `normalize_region_code()`
- `legacy_wati_lookup_phone()`
- `build_phone_identity()`
- `identity_from_contact_record()`
- `count_distinct_identity_keys()`
- `PhoneIdentity` dataclass (or equivalent typed structure)

Required behavior:

- parse valid international numbers into E.164
- use `default_phone_region` when the raw number is national-format
- fall back to:
  - `wati:<provider_contact_id>` when parsing fails but Wati contact identity is
    known
  - `raw:<digits>` when parsing fails and only raw digits remain
  - `missing` when no phone can be recovered
- derive `wati_lookup_phone` from E.164 using the legacy compatibility rule

Important scope guard:

- Port the library behavior, fixtures, and tests from `dialo-cx`
- Do **not** port app/database helpers, Streamlit wrappers, or daemon-specific
  entrypoints that were only one consumer of this library

### 3. Connector install region config

Port the connector-scoped region contract:

- add `default_phone_region` to `public.tenant_connectors`
- validate as uppercase ISO alpha-2 when present
- do **not** put it on `public.tenant`

Reason: phone interpretation is provider-install-specific, not workspace-global.

Concrete build implication:

- `WatiClient.from_supabase()` and any helper that loads connector config will
  need to read `default_phone_region`
- `setup` should optionally prompt for it
- `status` should display it

### 4. Canonical contact identity fields

Port the connector-owned identity fields from `dialo-cx silver.contacts` into
`Wati-Base canonical.contacts`:

- `phone_raw_primary`
- `phone_identity_key`
- `phone_e164`
- `normalization_status`
- `normalization_region`
- `wati_lookup_phone`

Also add the supporting indexes that matter for connector behavior:

- `(tenant_id, phone_identity_key)` where not null
- `(tenant_id, phone_e164)` where not null
- `(tenant_id, wati_lookup_phone)` where not null

Keep `phone_normalized` as the legacy compatibility key for uniqueness during
the transition.

Concrete migration note:

- existing live contacts in Filippa will not have these fields populated until a
  contact resync updates them
- build should rely on re-running `sync contacts` after schema changes rather
  than inventing a separate one-off migration command

### 5. Provider key mapping behavior

Port the multi-key Wati mapping behavior concept from `dialo-cx` into
`canonical.contact_provider_keys`.

At minimum ensure this table can represent:

- the canonical contact
- the provider contact ID
- the provider lookup phone
- which provider mapping is primary / latest

If the existing columns are not enough, extend the table rather than relying on
implicit behavior in `canonical.contacts`.

Pinned direction for Wati-Base:

- keep generic table naming (`provider_phone` is acceptable if we document it as
  the provider lookup phone)
- add the missing lifecycle fields from `dialo-cx`:
  - `is_primary`
  - `first_seen_at`
  - `last_seen_at`

This avoids losing the generic connector shape while still porting the real
many-to-one identity behavior that `dialo-cx` already proved out.

### 6. Contact import / promotion logic

Update contact import so Wati-Base no longer stops at raw digit stripping.

Required behavior:

1. Canonical contact rows still get created idempotently
2. `phone_normalized` uses the legacy Wati compatibility normalizer
3. `phone_raw_primary` preserves the preferred raw provider phone
4. `wati_lookup_phone` is stored explicitly
5. smart identity fields are populated from the shared parser using:
   - raw phone
   - connector `default_phone_region`
   - Wati provider contact ID

Important design constraint:

- Do not make canonical row creation depend on perfect `phonenumbers` parsing
- If parsing fails, canonical rows must still be created with safe fallback
  identity fields

Concrete implementation sequence:

1. `BatchContactImport.run()` stages raw contacts exactly as today
2. `process_contacts_to_canonical()` creates/updates canonical rows using the
   legacy Wati-compatible SQL normalizer
3. `BatchContactImport.run()` then loads the batch's staged contacts plus the
   connector `default_phone_region`
4. For each staged contact, Python builds a `PhoneIdentity`
5. The importer patches:
   - `canonical.contacts` by `contact_id`
   - `canonical.contact_provider_keys` by `provider_contact_id`

Deterministic winner rule when multiple provider records collapse to one
canonical contact:

- prefer a parsed E.164 identity over a fallback identity
- otherwise prefer the mapping marked primary
- otherwise prefer the most recently seen provider record

This keeps the import idempotent and makes the "best" identity stable.

### 7. Message fetch and message promotion behavior

Port the lookup rule all the way through message backfill:

- fetch messages using `wati_lookup_phone` rather than `phone_raw`
- join staged messages back to canonical contacts via the Wati-compatible lookup
  key
- keep the current idempotent `UNIQUE (tenant_id, wati_message_id)` behavior

This is the operational proof that the phone identity model is correct: the
same stored lookup phone must work for both dedupe and Wati message retrieval.

Pinned rollout safety rule:

- `BatchMessageImport` should query `coalesce(wati_lookup_phone, phone_raw)` for
  contact fetches during the transition, not hard-require `wati_lookup_phone`
  from day one
- message promotion should join on `coalesce(c.wati_lookup_phone,
  c.phone_normalized)` against the stored staged conversation lookup phone

That lets the rollout work on already-landed Filippa contacts while still
moving the steady-state path to `wati_lookup_phone`.

### 8. Tests and fixtures

Port the identity fixture coverage from `dialo-cx`, adapted to Wati-Base:

- add unit tests for the shared phone identity library
- bring over the fixture cases from
  `dialo-cx/tests/fixtures/phone_identity_cases.json`
- include explicit coverage for:
  - MX `52` vs `521`
  - US/GB E.164 normalization
  - ambiguous national-format fallback with `default_phone_region`
  - `wati:<id>` fallback
  - `raw:<digits>` fallback
  - `wati_lookup_phone` derivation

Repo reality note:

- Wati-Base currently has no dependency manifest and no `tests/` tree
- this mold should add a minimal, reproducible Python test surface rather than
  relying on ad hoc local `.venv` state

## Files To Modify In Wati-Base

| File | Action | Purpose |
|------|--------|---------|
| `database/init_db.sql` | Modify | add connector region field, contact identity fields, indexes, provider key fields if needed |
| `database/functions.sql` | Modify | port legacy Wati normalizer semantics and adapt promotion logic |
| `shared/phone_identity.py` | Create | neutral port of `dialo-cx` parser/identity helpers |
| `shared/batch_contact_import.py` | Modify | populate smart identity fields during/after contact sync |
| `shared/batch_message_import.py` | Modify | fetch messages via `wati_lookup_phone` |
| `shared/wati_client.py` | Maybe modify | only if needed for cleaner lookup-phone call sites; avoid unnecessary transport changes |
| `scripts/wati_supabase_cli.py` | Modify | surface connector region config where needed; keep status/setup aligned |
| `tests/unit/test_phone_identity.py` | Create | port/adapt identity tests |
| `tests/fixtures/phone_identity_cases.json` | Create | port/adapt fixture cases |
| `requirements.txt` | Create | pin runtime + test deps needed to reproduce `phonenumbers` and `pytest` locally |

## Out of Scope

Do **not** port these into this mold:

- Streamlit app code
- classifier / journey / jbuild logic
- `gold` analytics views
- app-owned database wrappers
- exact drift as a hard correctness dependency
- destructive/automatic contact merges

If we want a dedicated background identity refresh daemon or drift command
later, that can be a follow-up mold. This mold is about porting the connector's
core identity contract and making ingestion/backfill use it.

## Verification

### Unit verification

- Identity fixture tests pass in Wati-Base
- MX `52...` and `521...` cases collapse to the same Wati lookup phone
- E.164 and region-inferred cases produce stable `phone_identity_key`

### Schema verification

- `canonical.contacts` exposes the new identity fields
- `public.tenant_connectors` exposes `default_phone_region`
- `canonical.contact_provider_keys` exposes primary/seen metadata
- required indexes exist

### Live verification

Using Filippa test tenant `310131` on the self-hosted Supabase stack:

1. Re-run a small contact sync
2. Inspect a sample of contacts where the raw Wati phone varies by format
3. Confirm `phone_normalized`, `phone_e164`, `phone_identity_key`, and
   `wati_lookup_phone` are populated sensibly
4. Run a small message sync and confirm fetches use `wati_lookup_phone`
5. Verify no regression in:
   - idempotent contact upserts
   - idempotent message inserts
   - canonical message/contact joins
6. Verify transitional safety:
   - contacts without populated `wati_lookup_phone` can still backfill messages
     via fallback
   - after a resync, `wati_lookup_phone` becomes the steady-state path

## Implementation Notes

- Treat `dialo-cx/scripts/phone_identity.py` as the semantic source of truth for
  the Python parser; do not re-invent a lighter version in Wati-Base
- Treat `dialo-cx/database/05_functions.sql` as the semantic source of truth for
  the legacy Wati lookup normalizer; copy that behavior exactly into
  Wati-Base's `public.normalize_phone()`
- Keep this mold scoped to core ingest/backfill correctness. If the build starts
  pulling in an identity refresh daemon, exact drift check, or manual merge UX,
  split a follow-up mold instead of letting this one sprawl

## Related Molds

- `supawati-2` — explicitly deferred full phone normalization as future work
- archived `dialo-6` design notes — source of the connector-scoped identity
  contract and `default_phone_region` placement
