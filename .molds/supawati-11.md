<!-- molds
id: supawati-11
stage: land
resume_stage:
parent:
deps: supawati-10
priority: P1
created_at: 2026-04-28T00:00:00Z
updated_at: 2026-04-29T00:00:00Z
closed_at:
defer_until:
code_branch:
-->

# Make the identity rollout reproducible and repair legacy canonical contacts

## TL;DR

- `supawati-10` landed the new identity model, but existing deployments still
  need a real in-place upgrade path; `init_db.sql` alone does not alter live
  tables.
- The live Filippa retest proved that a contact resync is not enough: `61`
  canonical contacts still have null identity fields, `60` of those were not in
  the latest Wati contact batch, and one Wati contact is split across two
  canonical rows.
- Build an idempotent live-upgrade script plus a tenant-scoped repair command
  that backfills legacy identity fields from canonical data, repairs
  provider-key mismatches, and merges only the safe same-provider duplicates.
- Also harden `process_contacts_to_canonical()` so future syncs resolve existing
  provider identities before inserting by normalized phone, which prevents the
  same split from recurring.

## Problem

`supawati-10` updated the fresh-install schema and the live sync path, but it
did not finish the rollout story for already-running Supabase deployments.

The live Filippa retest exposed three concrete gaps:

1. Existing databases do not pick up the new identity columns by rerunning
   `database/init_db.sql`, because the file is mostly `create table if not
   exists` and `create index if not exists`. The retest only worked after a
   manual `ALTER TABLE` patch.
2. Repairing legacy rows cannot rely on `sync contacts`. After the schema patch
   and a fresh contact sync, `canonical.contacts` still had `61` rows with null
   identity fields. `60` of those rows were not present in the latest staged
   Wati contact batch, so batch-only enrichment can never reach them.
3. The rollout can create split canonical contacts when normalization changes.
   In Filippa there is already one Wati contact ID present on two canonical
   rows: the old row kept the pre-`supawati-10` phone key, while the new row was
   inserted with the corrected `521...` lookup phone. The provider-key row still
   points at the old contact, so future reads and writes are inconsistent.

The current implementation shape explains those failures:

- `shared/batch_contact_import.py` enriches only the current staged batch
- that enrichment finds rows by `canonical.contacts.wati_contact_id`
- `database/functions.sql` still promotes contacts by `(tenant_id,
  phone_normalized)` first, then separately upserts provider keys

That is good enough for fresh syncs on clean data, but it is not enough for a
live rollout where old canonical rows must be repaired without Wati, and where a
provider contact may already exist under an older normalized phone.

## Solution

Treat this as a rollout-hardening mold with three deliverables: an idempotent
live-upgrade SQL path, a tenant-scoped repair command for legacy canonical
rows, and a prevention fix in the normal contact promotion path.

### 1. Add an explicit in-place upgrade script for existing deployments

Keep `database/init_db.sql` as the source of truth for fresh installs, but add a
separate idempotent upgrade script for already-running stacks, likely
`database/upgrade_identity_schema.sql`.

That script should:

- `alter table ... add column if not exists` for the identity fields introduced
  by `supawati-10`
- create the supporting indexes that existing installs are missing
- add `public.tenant_connectors.default_phone_region` if absent
- add any repair-supporting index the new code needs, especially a lookup index
  on `canonical.contacts (tenant_id, wati_contact_id)` where `wati_contact_id`
  is not null
- be safe to re-run

The rollout instructions should explicitly say that live upgrades require:

1. apply `database/upgrade_identity_schema.sql`
2. reapply `database/functions.sql`
3. run the new repair command for the tenant
4. then run a normal `sync contacts` smoke

Do not hide that operator path inside tribal knowledge again.

### 2. Add a tenant-scoped canonical contact repair path

Introduce a real repair surface, not another one-off manual SQL patch. The
right shape is a new shared repair module plus a CLI entrypoint:

- new shared module: `shared/contact_identity_repair.py`
- new CLI surface: `wati-supabase repair contacts --tenant-id <id> [--dry-run]`

The repair path must work from existing canonical data and provider-key data,
without calling the Wati API. That is required because most of the broken legacy
rows are not in the latest staged Wati batch.

Required behavior:

1. Load canonical contacts for the tenant, plus their Wati provider keys and
   message counts
2. For each row, derive the best available phone inputs from:
   - `phone_raw_primary`
   - `phone_raw`
   - `canonical.contact_provider_keys.provider_phone`
3. Rebuild identity fields with `shared/phone_identity.py`, using connector
   `default_phone_region` when present
4. Backfill missing fields on rows that are simply incomplete but not
   duplicated
5. Detect and repair the safe duplicate class this rollout already created:
   multiple canonical rows for the same Wati `provider_contact_id`
6. Reattach provider keys and messages to the chosen survivor, then delete the
   loser row when the merge is safe

The command should record work in `ops.import_jobs` with a new
`job_type='contact_identity_repair'`, print a compact summary, and be idempotent
on repeated runs.

### 3. Prevent future splits in the normal sync path

The repair command fixes existing bad state, but it is not enough if a future
`sync contacts` can recreate the same split.

Update `public.process_contacts_to_canonical()` so the resolver order becomes:

1. existing Wati provider-key match on `(tenant_id, provider='wati',
   provider_contact_id)`
2. fallback canonical match on `(tenant_id, phone_normalized)`
3. insert new canonical row only when neither match exists

This means the provider identity becomes the first-class contact anchor during
promotion. If a Wati contact already exists in the system under an older
normalized phone, the existing canonical row should be updated instead of
creating a second row.

The `canonical.contact_provider_keys` upsert must also be hardened so conflict
resolution can move the provider key to the resolved survivor contact when
needed, not just refresh `provider_phone`.

### 4. Keep automatic merging conservative

Do not try to solve every possible fuzzy identity merge in this mold. For this
pass, automatic merging should be limited to the duplicate class we have real
evidence for:

- same `provider_contact_id` attached to more than one canonical contact, or
- a provider-key row whose `contact_id` disagrees with the canonical row that
  now owns that same `wati_contact_id`

Winner selection should be deterministic and documented. A practical order is:

1. row whose `phone_normalized` already matches the computed
   `wati_lookup_phone`
2. row with richer identity fields already present
3. row with more messages attached
4. older `created_at` as final tie-break

If the repair logic encounters a conflict outside that safe class, it should
report and skip it rather than guessing.

### 5. Refactor the current batch enrichment onto the shared repair logic

`shared/batch_contact_import.py` should stop owning bespoke enrichment logic.
Instead, keep one shared repair/enrichment implementation that can run in two
modes:

- batch-scoped enrichment right after `sync contacts`
- tenant-wide legacy repair from the new CLI command

That avoids having two identity repair paths drift apart again.

## Files to Modify
| File | Action | Done | Purpose |
|------|--------|------|---------|
| `database/upgrade_identity_schema.sql` | Add | yes | Idempotent live-upgrade script for existing deployments created before `supawati-10` |
| `database/init_db.sql` | Modify | yes | Keep fresh-install schema current and add any repair-supporting index, especially `wati_contact_id` lookup support |
| `database/functions.sql` | Modify | yes | Harden `process_contacts_to_canonical()` to resolve provider identity first; add a safe helper for moving references from loser contact to survivor contact during repair |
| `shared/contact_identity_repair.py` | Add | yes | Shared planner/executor for batch enrichment, legacy backfill, duplicate detection, and conservative merges |
| `shared/batch_contact_import.py` | Modify | yes | Replace bespoke `_enrich_identity_fields()` flow with the shared repair logic |
| `scripts/wati_supabase_cli.py` | Modify | yes | Add `repair contacts` command, dry-run support, and operator-facing summary output |
| `tests/unit/test_contact_identity_repair.py` | Add | yes | Cover winner selection, provider-key reassignment, idempotent backfill, and skip-on-unsafe-conflict behavior |
| `README.md` | Modify | yes | Document the live-upgrade sequence and the new repair command |

## Verification

- Unit tests cover:
  - enriching a legacy contact with no identity fields
  - repairing the observed same-provider duplicate shape
  - moving provider keys to the survivor contact
  - preserving or reattaching messages during merge
  - idempotent repeated runs
  - skipping and reporting an unsafe conflict instead of auto-merging it
- On the Filippa self-hosted test tenant, after applying the upgrade script and
  running `wati-supabase repair contacts --tenant-id 310131`:
  - `canonical.contacts` rows with null `phone_identity_key` drop from `61` to
    `0`
  - duplicate `wati_contact_id` groups drop from `1` to `0`
  - provider-key/contact mismatch count drops from `1` to `0`
  - total canonical contact count does not increase
- Re-running the same repair command produces a no-op or zero-change summary
- Running `sync contacts --tenant-id 310131 --limit 1` after the repair does not
  recreate the split-contact problem

Observed live result:

- first dry-run: `examined=261`, `contact_updates=60`, `provider_key_updates=1`,
  `merge_groups=1`, `merged_contacts=1`, `conflicts=0`
- first live repair fixed the tenant state but surfaced one provider-key insert
  bug after the merge step; that path is now fixed and the failed job is
  preserved in `ops.import_jobs`
- post-fix rerun: `examined=260`, `contact_updates=0`,
  `provider_key_updates=0`, `merge_groups=0`, `merged_contacts=0`,
  `conflicts=0`
- post-repair sync smoke completed without reintroducing duplicate Wati contact
  groups or provider-key mismatches; that batch also inserted `100` genuinely
  new provider keys from the current Wati first page

## Implementation Notes

- The live evidence matters here. The retest already proved that:
  - manual `ALTER TABLE` work was required
  - `60/61` broken rows are outside the latest staged Wati contact batch
  - there is at least one real duplicate canonical contact for the same Wati
    provider contact
- The repair command should not depend on staging tables, because staging is
  batch-scoped and transient relative to this repair need
- When rebuilding identity for a legacy row, prefer `provider_phone` as an
  explicit lookup hint when it exists, but still derive the canonical E.164 and
  identity key from the best raw phone input
- The merge helper in SQL should move dependent rows safely:
  - `canonical.messages`
  - `canonical.contact_provider_keys`
  - then delete the losing `canonical.contacts` row only when nothing still
    references it
- The design goal is reproducible operations on live installs, not just cleaner
  fresh bootstraps

## Notes

- This mold intentionally stops short of broad fuzzy contact merging by shared
  phone identity across different provider contact IDs. That broader policy can
  be designed later if needed; the immediate production issue is the same-Wati
  contact split introduced by the identity rollout itself.
- QA review completed on commit `64455c9706ad4b416247b6c0f6ca6aa5f2a9a116`.
- Native `molds qa-pass` was unavailable because this repo still reports
  legacy molds config. QA evidence was recorded manually in this file.
- Additional QA checks beyond the build pass:
  - `pytest -q tests/unit/test_phone_identity.py tests/unit/test_contact_identity_repair.py`
    passed with `11/11`
  - live Filippa tenant remained healthy after the repair rerun and post-repair
    sync smoke: null identity rows `0`, duplicate `wati_contact_id` groups `0`,
    provider-key/contact mismatches `0`
  - rolled-back SQL harness verified `public.merge_canonical_contacts()`
    reattaches messages/provider keys and deletes the loser row
  - rolled-back SQL harness verified `public.process_contacts_to_canonical()`
    updates the existing provider-matched contact instead of inserting a split
