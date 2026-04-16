<!-- molds
id: dialo-8
stage: design
resume_stage: 
parent: 
deps: dialo-6
priority: P2
created_at: 2026-04-15T18:17:29Z
updated_at: 2026-04-15T19:10:16Z
closed_at: 
defer_until: 
code_branch: 
-->


# Productize Wati to Supabase connector — Phase B schema rename and cutover

## TL;DR

- This mold is Phase B only: physically rename the live data-layer surface from
  `bronze/silver/gold` to `staging/canonical/analytics`, and move connector
  control-plane objects into `ops`.
- It depends on `dialo-6` landing first. Do not start this before ownership
  cleanup, `tenant_connectors`, neutral shared runtime extraction, and
  connector-vs-journey separation are already in place.
- This is the higher-blast-radius prerelease mold: code, SQL, grants, tests,
  docs, and self-hosted PostgREST exposure all change together.
- It is acceptable for this to be a little rough because it is prerelease, but
  it still needs one deliberate coordinated cutover plan rather than ad hoc
  dual-write hacks.

## Reader Guide

Read this mold only after Phase A makes the architecture legible.

Suggested reading order:

1. `docs/PRODUCT_MAP.md`
2. `.molds/dialo-6.md`
3. `wati-to-supabase/runtime/phase-b-rename-cutover.md`
4. current schema/grants touchpoints:
   - `database/02_bronze_layer.sql`
   - `database/03_silver_layer.sql`
   - `database/06_webhook_logging.sql`
   - `database/07_gold_layer.sql`
   - `database/08_grants.sql`
5. runtime callsites:
   - `app/database.py` or its successor
   - connector scripts under `scripts/`

This mold is supposed to teach rollout discipline. It is not a debate about
product boundaries anymore.

## Problem

After Phase A, the repo can have the right product boundaries while still
running on legacy physical schema names:

- `bronze`
- `silver`
- `gold`
- connector control-plane tables in `public`

That is acceptable temporarily, but it leaves the repo and deployed system with
old naming that no longer matches the product architecture.

The physical rename is materially risky because the current names are hard-coded
across:

- SQL functions, triggers, views, comments, and grants
- app and script callsites using `client.schema("bronze"|"silver"|"gold")`
- tests that assert literal schema names
- operator docs and troubleshooting queries
- self-hosted grants and PostgREST exposed schemas in `database/08_grants.sql`

So this mold is not architecture cleanup. It is a real migration and rollout
task.

The repo scaffold should be treated as part of the working surface for this
mold:

- `docs/PRODUCT_MAP.md`
- `wati-to-supabase/runtime/phase-b-rename-cutover.md`

Those files should carry the rename/cutover narrative so the implementation is
not buried only in SQL diffs.

## Why Phase B Is Separate

This phase is separate because renaming live schema surfaces is deceptively
mechanical. It looks like search-and-replace work, but the real job is keeping
the deployed system coherent while every assumption about schema names changes
at once.

What this mold teaches is:

- why architecture cleanup should happen before a rename
- why deploy sequencing matters as much as SQL correctness
- why tests, grants, and self-hosted exposure are part of the runtime surface
  too

## Solution

Implement a coordinated physical rename and cutover after `dialo-6` lands.

Target end state:

- `bronze` -> `staging`
- `silver` -> `canonical`
- `gold` -> `analytics`
- connector control-plane objects moved into `ops`

### Locked build posture

1. Do not dual-write long-term
   - avoid maintaining old and new schemas in parallel longer than necessary
   - avoid compatibility layers for write-heavy paths unless one is proven
     necessary for a specific deployment step

2. Prefer one coordinated cutover
   - code and SQL should already be prepared on the branch
   - migration should happen in one deploy window
   - self-hosted PostgREST/grants must be updated in the same release

3. Keep the blast radius contained
   - no new product-boundary debates in this mold
   - no reopening ownership questions already decided in `dialo-6`
   - this mold is about renaming, rewiring, and deploying safely

### Expected implementation areas

1. SQL object rename / recreation
   - schemas
   - tables
   - views
   - functions
   - triggers
   - comments

2. Code callsite sweep
   - `client.schema("bronze")` -> `client.schema("staging")`
   - `client.schema("silver")` -> `client.schema("canonical")`
   - `client.schema("gold")` -> `client.schema("analytics")`
   - `public` control-plane table callsites -> `ops`

3. Self-hosted access/grants
   - `database/08_grants.sql`
   - any deploy notes or environment docs that refer to exposed schemas
   - verify PostgREST visibility and access after cutover

4. Docs/tests
   - operator docs
   - webhook README
   - import READMEs
   - unit tests with literal schema assertions
   - top-level architecture docs

### Recommended migration sequence

1. Start from a repo state where `dialo-6` is integrated
2. Update SQL objects and code callsites on the branch
3. Update grants/tests/docs in the same branch
4. Prepare a deployment checklist for hosted and self-hosted paths
5. Execute one coordinated cutover
6. Verify:
   - webhook ingest
   - batch contact import
   - batch message backfill
   - daemon tasks
   - canonical reads from app code

## Cutover Mindset

The correct mindset for this mold is:

- minimize ambiguity
- minimize time spent in half-renamed states
- keep recovery simple

That means preferring one coordinated cutover over clever compatibility layers.
Compatibility shims feel safer, but they often create more unknowns than they
remove, especially in write-heavy paths.

## Common Failure Modes

- updating SQL names but forgetting grants or exposed schemas
- renaming app callsites but missing script/daemon callsites
- changing docs after the fact instead of using docs as part of the cutover
  checklist
- reopening Phase A boundary debates in the middle of rollout work
- leaving hidden legacy references to `bronze`, `silver`, or `gold` in tests or
  troubleshooting notes

## Files to Modify
| File | Action | Purpose |
|------|--------|---------|
| `database/02_bronze_layer.sql` | refactor | Physical rename to staging and staging contract |
| `database/03_silver_layer.sql` | refactor | Physical rename to canonical for connector-owned normalized data |
| `database/06_webhook_logging.sql` | refactor | Move connector control-plane objects into ops and rewire docs/comments |
| `database/07_gold_layer.sql` | refactor | Physical rename to analytics |
| `database/08_grants.sql` | refactor | Update grants and exposed schema assumptions |
| `docs/PRODUCT_MAP.md` | refactor | Keep top-level product and layer language aligned after cutover |
| `wati-to-supabase/runtime/phase-b-rename-cutover.md` | create/refine | Cutover checklist and blast-radius notes |
| `app/database.py` or successor neutral package | refactor | Rename schema callsites and ops table targets |
| `scripts/` connector modules | refactor | Rename schema callsites and RPC targets |
| `tests/unit/test_app_database.py` | refactor | Update schema expectations |
| `tests/unit/test_sql_contracts.py` | refactor | Update literal SQL/schema expectations |
| `README.md` | refactor | Update architecture and setup language |
| `scripts/BATCH_IMPORT_README.md` | refactor | Update operational docs and queries |
| `supabase/functions/wati-webhook/README.md` | refactor | Update webhook flow docs and troubleshooting |

## Verification

- All connector code paths run against `staging`, `canonical`, and `ops`
- No critical runtime path still references `bronze`, `silver`, or `gold`
- Webhook ingest works after cutover
- Batch contact import works after cutover
- Batch message backfill works after cutover
- Daemon tasks work after cutover
- Self-hosted PostgREST can see and access the renamed schemas
- Tests and docs are updated to the new names
- Rename/cutover scaffold docs reflect the actual sequence used in build

## Implementation Notes

- This mold is appropriate to hand to Christian too, but it is the riskier of
  the two connector molds.
- Because this is prerelease, perfection is not the goal; coherence is.
  If something fails, recovery is manageable as long as the work stays within
  this mold's explicit cutover scope.
- If implementation pressure reveals an unavoidable need for a temporary
  compatibility shim, it should be narrowly documented and removed quickly.
- A good implementation of this mold leaves behind a cleaner deployment story,
  not just cleaner names.
