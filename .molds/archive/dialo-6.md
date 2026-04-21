<!-- molds
id: dialo-6
stage: closed
resume_stage:
parent:
deps:
priority: P2
created_at: 2026-04-15T16:28:23Z
updated_at: 2026-04-21T00:00:00Z
closed_at: 2026-04-21T00:00:00Z
defer_until:
code_branch:
-->

# Productize Wati to Supabase connector — Phase A boundary cleanup

## TL;DR

- This mold is Phase A only: clean up ownership, runtime boundaries, tenant vs
  connector config, and connector `ops`, without physically renaming the live
  `bronze/silver/gold` schemas yet.
- The target vocabulary remains `staging -> canonical -> analytics`, with `ops`
  for runtime state and observability, but the physical rename is deferred to
  follow-on mold `dialo-8`.
- Keep the product explicitly Wati-specific. The goal here is a cleaner
  standalone `Wati -> Supabase` connector boundary, not premature
  generalization.
- The main build outcome should be: Christian can implement a safer prerelease
  architecture cleanup first, and then tackle the separate rename/cutover mold.

## Reader Guide

This mold is intentionally explanatory. It is meant to teach the architecture
while it scopes the build.

If you are new to this repo, read in this order:

1. `docs/PRODUCT_MAP.md`
2. `wati-to-supabase/README.md`
3. `wati-to-supabase/contracts/canonical-contract.md`
4. `wati-to-supabase/contracts/staging-semantics.md`
5. `wati-to-supabase/contracts/tenant-connectors-model.md`
6. `wati-to-supabase/runtime/phase-a-boundary-cleanup.md`
7. `wati-to-supabase/cli/README.md`
8. then inspect the legacy implementation surface in:
   - `scripts/`
   - `supabase/functions/wati-webhook/`
   - `database/`
   - `app/database.py`
   - `app/pages/2_Admin.py`
   - `app/auth.py`

Read this mold as:

- a boundary document first
- a migration-preparation document second
- not a request to rename everything immediately

## Problem

Dialo currently contains a real Wati connector product, but it is not packaged
or documented like one.

Today the connector logic is scattered across:

- `scripts/wati_client.py`
- `scripts/batch_contact_imports.py`
- `scripts/batch_messages_import.py`
- `scripts/task_daemon.py`
- `scripts/daemon_tasks/`
- `scripts/phone_identity.py`
- `supabase/functions/wati-webhook/`
- Wati-specific tables and transforms in `database/`

This already has independent value, but several practical issues make it hard to
present or ship as a standalone connector:

- runtime code still imports `app/database.py`, which is an app-owned helper
  module with Streamlit caching behavior
- tenant configuration in `public.tenants` mixes workspace data with Wati
  connector credentials
- `database/03_silver_layer.sql` currently mixes connector-owned contacts and
  messages with non-connector tables like `journey_intent_results` and
  classifier config state
- connector-owned operational tables currently live in `public` and
  connector-facing naming is embedded in grants, tests, docs, and self-hosted
  PostgREST schema exposure
- operator UX still lives inside the Dialo app instead of a connector-focused
  admin surface or docs-first runtime
- the repo does not yet make the connector boundary obvious to contributors or
  users

The goal is not to make Wati disappear from the schema. The goal is to give the
Wati ingestion subsystem a clean product boundary and ownership model.

The current medallion naming (`bronze/silver/gold`) is also not ideal for an
OSS connector product. It is jargon-heavy and does not communicate the product
contract as clearly as explicit layer names would.

Scaffold rails now exist in the repo and should be treated as the first place
to put connector-facing design artifacts before code is moved:

- `docs/PRODUCT_MAP.md`
- `wati-to-supabase/contracts/canonical-contract.md`
- `wati-to-supabase/contracts/staging-semantics.md`
- `wati-to-supabase/contracts/tenant-connectors-model.md`
- `wati-to-supabase/runtime/phase-a-boundary-cleanup.md`
- `wati-to-supabase/runtime/phase-b-rename-cutover.md`
- `wati-to-supabase/cli/README.md`
- `scripts/wati_supabase_cli.py`
- `./wati-supabase`
- `shared/runtime/`

Ground-truth code audit findings that materially affect scope:

- `database/03_silver_layer.sql` currently contains both connector-owned tables
  (`contacts`, `messages`, Wati key mappings) and non-connector tables
  (`journey_intent_results`) plus public classifier config tables
- `database/07_gold_layer.sql` is entirely application-facing analytics today, not
  connector-owned reporting
- connector-owned control-plane tables live in `public`, not a dedicated `ops`
  schema
- self-hosted schema exposure/grants explicitly name `bronze,silver,gold` in
  `database/08_grants.sql`
- app code, scripts, tests, and docs all hard-code current schema names

These findings mean a clean connector build must separate ownership first, and
that is the purpose of this Phase A mold.

## Why Phase A Exists

Phase A exists because architecture cleanup and schema renaming are different
kinds of work.

This phase is about answering:

- what is the connector product boundary
- what belongs to the connector versus the application layer
- where shared runtime code should live
- how workspace identity differs from connector installation state
- what the connector guarantees as its canonical output

It is not yet about changing every schema name in the system.

That distinction matters because the repo is already operational. If ownership
is still muddy, a rename only spreads ambiguity to more places. A clean rename
needs a clean architecture target first.

## Solution

Design and implement the connector as a first-class product rooted at
`wati-to-supabase/`.

Early design goals:

1. Define the connector boundary
   - owns Wati credentials, webhook ingestion, contact/message backfills,
     provider phone lookup behavior, sync progress, drift checks, and daemon
     tasks
   - produces canonical contacts/messages in Supabase plus provider-specific
     mapping tables

2. Extract connector runtime ownership
   - move shared DB access out of `app/database.py` into a neutral module that
     both products can import
   - remove `sys.path` hacks that currently tie scripts to the app layout

3. Clarify multitenant ownership
   - keep one shared `tenant_id`
   - move Wati-specific credentials/config toward a connector-scoped table
     instead of keeping all of them on `public.tenants`

4. Clarify product docs and setup
   - make the standalone connector story obvious from the repo root
   - document what Supabase schema contract it guarantees
   - document deployment/runtime modes separately from the application layer

5. Clarify connector admin surface ownership
   - define the CLI-first operational surface the connector owns
   - stop using the shared Streamlit admin page as the long-term connector
     control plane
   - keep optional watcher commands simple and scriptable

6. Standardize the conceptual data-layer language
   - `bronze` becomes `staging`
   - `silver` becomes `canonical`
   - `gold` becomes `analytics`
   - runtime state like job rows, webhook logs, drift runs, and connector
     health should live conceptually under `ops`

Naming rationale:

- `staging` describes the connector landing area for raw provider data
- `canonical` describes the stable normalized conversation contract shared with
  downstream consumers
- `analytics` describes downstream derived views and reporting projections
- `ops` cleanly separates operational control-plane tables from the data model

The middle layer must be named for the thing downstream systems depend on, not
for the transformation process itself. `canonical` is a better long-term
contract name than `normalize` or `transform`.

### Phase A build scope

This mold should build only the boundary-cleanup slice.

Included in Phase A:

- introduce `public.tenant_connectors`
- introduce a real `ops` schema and move connector-owned control-plane objects
  there
- extract neutral shared runtime/data-access code out of `app/database.py`
- make the connector-owned canonical contract explicit
- make the connector-owned CLI/admin UX boundary explicit
- separate connector-owned canonical objects from non-connector objects by
  ownership and file structure
- update docs so the connector product boundary is explicit

Excluded from Phase A:

- physical schema rename from `bronze/silver/gold`
- broad codebase-wide callsite rename sweep
- grants/PostgREST cutover to new schema names
- coordinated deploy window for physical schema rename

Those belong to `dialo-8`.

### What The Implementer Should Learn From This Phase

If this mold is implemented well, the implementer should come away with a clear
mental model of four boundaries:

1. connector runtime vs application layer
2. real-time ingest vs batch/repair runtime
3. canonical business data vs operational control-plane data
4. workspace state vs connector-installation state

That learning outcome is part of the value of this mold. The code changes should
make those boundaries visible in the repo rather than keeping them implicit in
tribal knowledge.

#### 14. Connector admin UX should be CLI-first in Phase A

The current Streamlit app already contains connector operations, but it is the
wrong long-term home for them because it mixes connector and application-layer
concerns:

- `app/pages/2_Admin.py` combines connector operations with application config
- `app/auth.py` still expresses Wati installation assumptions directly in the
  main app auth/setup path

Locked direction:

- do not build a separate connector frontend in this phase
- do not treat the shared Streamlit admin page as the connector product
- make the connector admin surface CLI-first
- name the command `wati-supabase`
- support optional watcher commands, but keep them simple polling/console UX

Near-term connector CLI ownership should include:

- first-time connector setup / initial contact import
- drift and sync health
- contact import
- message backfill
- job history and logs
- daemon/webhook health
- connection testing / doctor checks

This is important for handoff because the implementer should be able to see the
connector boundary in the admin surface, not just in database files and scripts.

Recommended command posture:

- `wati-supabase setup`
- `wati-supabase status`
- `wati-supabase sync contacts`
- `wati-supabase backfill messages`
- `wati-supabase drift check`
- `wati-supabase logs`
- `wati-supabase doctor`
- `wati-supabase daemon run`
- optional watch variants like `wati-supabase watch status`

### Locked design decisions

#### 1. Real-time webhook ingestion stays "pure Supabase"

Do not introduce a separate always-on worker just to promote webhook events from
raw ingress into canonical rows.

Target design:

- the Supabase Edge Function receives the webhook and writes raw ingress rows
- Postgres-owned promotion logic converts those raw rows into canonical contacts
  and messages
- the daemon remains for scheduled and repair-oriented tasks, not for the
  primary real-time path

Reasoning:

- one less runtime to deploy and monitor
- stronger idempotency when dedupe/upsert happens directly at the database
  boundary
- lower operational complexity for self-hosted users
- the webhook transform is currently deterministic and lightweight enough to fit
  the database-owned promotion model

This means the connector runtime model should be:

- real-time path: Edge Function + Postgres
- batch/scheduled path: Python scripts + daemon

Not:

- real-time path: Edge Function + queue + separate promotion worker

The daemon still matters, but for:

- exact drift checks
- identity refresh / reconciliation
- periodic repair and health checks
- future scheduled connector tasks

It is not the canonical event-consumer loop.

#### 2. Staging semantics must distinguish events from snapshots

The current raw-layer story is internally inconsistent: comments say "keep all
raw data forever" while constraints still upsert away duplicates for contacts
and messages.

First-principles staging model:

- message ingress is an event stream
- contact ingestion is a snapshot sync

So staging should have different semantics for each:

1. `staging` message events
   - append-only logical event log
   - one row per provider message/event identity
   - immutable payload once captured
   - duplicate deliveries are handled idempotently, but the first-seen raw event
     remains the canonical ingress row

2. `staging` contact snapshots
   - latest-seen provider state per provider contact identity
   - safe to upsert current snapshot state
   - import runs and `last_seen_at` belong here
   - if historical snapshot analysis is ever needed, add explicit history rather
     than pretending the latest-state table is append-only

This is the production-ready split because messages and contacts are different
kinds of source data. Treating both as the same kind of "raw forever" storage
creates confusion and weak guarantees.

#### 3. The canonical contract is the connector's public product surface

`canonical` is the stable, documented output contract that the connector
guarantees.

Downstream consumers could be:

- BI/reporting tools
- CRM sync jobs
- custom agents
- dbt models
- internal tooling

So `canonical` must be documented explicitly and independently of any one app.

Minimum contract principles:

- contacts and messages are provider-agnostic at the row-semantics level
- provider-specific identities live in mapping tables, not in the core meaning
  of the canonical tables
- one canonical contact groups one coherent customer conversation identity
- one canonical message row represents one real conversational unit
- `direction` semantics are stable and connector-guaranteed
- timestamps are connector-guaranteed and queryable in chronological order

Suggested canonical surface to document:

- `canonical.contacts`
  - `contact_id`
  - `tenant_id`
  - stable identity key
  - display/raw phone fields
  - name / source metadata
  - canonical/merged state

- `canonical.contact_provider_keys`
  - provider contact IDs
  - provider lookup phones
  - mapping from provider identities to canonical contacts

- `canonical.messages`
  - `message_id`
  - `tenant_id`
  - `contact_id`
  - `timestamp`
  - `direction`
  - `message_type`
  - `text`
  - provider message ID / provider conversation ID as reference fields

Downstream consumers depend on this contract, but do not define it.

#### 4. Keep Python reconciliation as repair/enrichment, not core ingest

`phonenumbers`-based identity refresh remains valuable, but it should be framed
as asynchronous repair/enrichment over canonical rows, not the thing that makes
real-time ingestion function at all.

That gives the connector two clean guarantees:

- webhook path works immediately with pure Supabase runtime ownership
- background reconciliation improves identity quality and drift visibility

This is safer than requiring every real-time event to pass through a second
runtime before canonical rows exist.

#### 5. `ops` needs explicit privacy, retention, and replay rules

The current webhook observability layer stores full headers and payloads. That
is too loose for a production OSS connector.

First-principles `ops` policy:

- keep enough raw data to debug and replay connector behavior
- do not keep secrets or unnecessary sensitive metadata
- retain high-volume raw operational logs for a bounded time window
- retain summarized processing outcomes longer than raw payloads

Locked direction:

- Authorization headers must never be stored verbatim
- store either:
  - a redacted/safelisted header subset, or
  - no headers at all unless a concrete debugging need exists
- raw webhook payloads may be stored for replay/debug, but under a configurable
  retention TTL
- default raw webhook retention should be short and operational, not archival
  by default: `30 days` is the recommended starting point
- processing outcome rows, aggregate health views, and daemon task summaries can
  be retained longer because they are lower-risk and lower-volume

Replay principle:

- replay should work from stored payload + tenant/connector identity
- replay should not depend on archived raw HTTP headers

This means the build should separate:

- raw replay/debug payload storage
- operational summaries and health state

rather than treating them as one forever-log.

#### 6. Exact drift is a health check, not a correctness dependency

Exact drift currently requires a full Wati contact crawl, which is expensive and
should not be promoted to a normal ingestion requirement.

Locked direction:

- exact drift remains an `ops` integrity check
- canonical writes must not depend on a clean exact drift result
- the UI should read persisted drift state, not trigger live crawls
- exact drift should run at low frequency only

Recommended default posture:

- run exact drift at most daily for active tenants
- allow manual one-shot execution for operator debugging
- use cheaper health indicators for day-to-day status, such as:
  - recent webhook activity
  - batch import freshness
  - webhook processing failures
  - identity refresh coverage

Exact drift is valuable because it proves parity. It is not the thing that keeps
the connector healthy in normal operation.

#### 7. Multi-tenant connector installs need their own table

Keep one shared `tenant_id`, but stop using `public.tenants` as the direct home
for Wati installation details.

Locked direction:

- `public.tenants` remains the workspace/account table
- connector installs move to a separate table, recommended name:
  `public.tenant_connectors`
- one tenant can have zero or more connectors over time
- for the Wati connector phase, enforce at most one active Wati installation per
  tenant via a uniqueness rule on `(tenant_id, provider)` or equivalent active
  install constraint

Suggested fields for `public.tenant_connectors`:

- `connector_id`
- `tenant_id`
- `provider` (`wati`)
- provider workspace/account identifier
- encrypted API token or secret reference
- provider phone number(s)
- webhook URL / webhook status
- `default_phone_region`
- install status / last successful sync / last error
- timestamps

Important placement decision:

- `default_phone_region` belongs with the connector installation, not the
  workspace row, because it is part of provider-data interpretation

This keeps the workspace model clean while still preserving one shared tenant
namespace for the connector and downstream products.

#### 8. Shared runtime code must move to a neutral package

The connector currently depends on `app/database.py`, which is the wrong module
owner for a standalone product.

Locked direction:

- create a neutral shared runtime/data-access package for non-UI code
- Streamlit caching wrappers stay in the app layer only
- scripts, daemon tasks, and future connector modules import the neutral
  package, not the app

Recommended package shape:

- `shared/` or equivalent top-level neutral module
- Supabase client creation
- tenant/connector reads
- canonical reads/writes
- `ops` task run helpers
- staging/canonical promotion helpers that are not UI concerns

This should eliminate:

- `sys.path` hacks in scripts
- accidental Streamlit imports in connector runtimes
- unclear ownership between product code and app code

#### 9. Identity refresh is permanent, but must stay non-destructive

Identity reconciliation should not be treated as temporary scaffolding only.
Provider data is messy enough that a background quality pass remains useful even
after the initial migration work is complete.

Locked direction:

- keep identity refresh as a permanent enrichment/repair task
- allow it to:
  - fill canonical identity fields
  - refresh provider lookup phones
  - refresh provider identity mappings
  - emit coverage and fallback metrics
- do not allow it to auto-merge contacts or make destructive contact surgery
  without explicit operator action

So the permanent contract is:

- ingest creates usable canonical rows immediately
- reconciliation improves data quality asynchronously
- destructive identity decisions stay operator-controlled

This is the safer production stance for a connector that may be fed messy real
customer data.

#### 10. `ops` should become a real schema, not just a concept

The connector already has a control plane:

- webhook receipt and processing logs
- import job tracking
- daemon task runs
- health/debug views and functions

Keeping these in `public` muddies the product boundary.

Locked direction:

- introduce a real `ops` schema
- move connector-owned control-plane tables and views there
- keep `public` for shared workspace/account tables and product-neutral
  top-level metadata

Target `ops` ownership:

- `ops.import_jobs`
- `ops.daemon_task_runs`
- `ops.webhook_logs`
- `ops.webhook_processing_log`
- `ops.webhook_processing_status`
- connector health/debug functions

Why this is the right production choice:

- keeps control-plane data out of user-facing business schemas
- makes grants and exposure easier to reason about
- aligns with the product story: connector owns `staging`, `canonical`, and
  `ops`

#### 11. `canonical` must not include non-connector tables

The current `silver` schema is not a pure connector-owned normalized model. It
also contains:

- `silver.journey_intent_results`
- tenant classifier config tables and related app state in the same layer file

Locked direction:

- `canonical` for the connector product should only include connector-owned
  normalized conversation data and helper views
- non-connector tables are explicitly out of connector scope

Connector-owned `canonical` target:

- `canonical.contacts`
- `canonical.contact_provider_keys`
- `canonical.messages`
- helper views like active contacts / unique contact IDs if they remain generic

Not connector-owned:

- classification results
- classifier config versions
- segment definitions

Implication:

- the current `database/03_silver_layer.sql` file must be split by ownership
  before the end state is clean
- actual physical `silver -> canonical` rename is blocked until the non-connector
  tables are extracted or otherwise relocated

This is the most important hidden constraint surfaced by the code audit.

#### 12. Physical schema rename should be a coordinated cutover, not dual-write

The rename surface is broad:

- `client.schema(\"bronze\"|\"silver\"|\"gold\")` callsites
- SQL functions, triggers, and comments
- grants in `database/08_grants.sql`
- self-hosted `PGRST_DB_SCHEMAS` configuration
- tests that assert literal schema names
- docs and operator troubleshooting queries

Locked direction:

- do not attempt long-lived dual writes between old and new schemas
- do not rely on compatibility views for write-heavy paths like upserts unless a
  specific path proves it is necessary
- prefer a coordinated cutover once ownership is clean and code is updated

Recommended migration strategy:

1. split ownership first
   - separate connector-owned canonical tables from non-connector tables
   - create neutral shared runtime module
   - introduce `tenant_connectors`
   - move control-plane tables into `ops`

2. prepare the rename branch
   - update all code to the new schema names
   - update SQL objects, grants, tests, docs, and deployment notes

3. perform one coordinated deploy/migration window
   - pause writers if needed
   - apply schema rename / object moves
   - update self-hosted PostgREST exposed schema list
   - deploy code that already points at the new names
   - verify webhook ingest, backfill, and canonical reads immediately

Why:

- dual-write across schemas adds more failure modes than it removes
- this repo controls both the SQL and the application callsites
- a single coordinated cutover is simpler to reason about for a self-hosted
  connector product

#### 13. The build should be phased, and the mold may need to split if scope pressure rises

`dialo-6` is now carrying:

- product boundary cleanup
- runtime module extraction
- tenant/connector schema split
- ops schema introduction
- canonical contract definition
- eventual physical schema rename

That is viable as one design document, but it may be too much as one build
slice.

Locked direction:

- treat the design as one coherent target architecture
- prefer phased implementation
- if the first build pass starts mixing neutral runtime extraction with physical
  schema rename risk, split the build scope before implementation rather than
  pushing through a giant migration

Natural split point if needed:

- Phase A: product boundary cleanup, neutral shared runtime, `tenant_connectors`,
  `ops` schema, canonical contract documentation
- Phase B: physical `bronze/silver/gold -> staging/canonical/analytics` rename
  after ownership is clean

### Concrete handoff note

This mold is the safer first implementation ticket to hand to Christian.

Why:

- it improves architecture and ownership without taking on the full blast radius
  of a live schema rename
- failures in this phase are easier to recover from
- it makes the follow-on rename mold more deterministic

If Christian completes this mold cleanly, the rename/cutover work in `dialo-8`
becomes much easier to execute or assist on.

Non-goals for this mold:

- making the connector provider-agnostic
- redesigning the classifier contract
- moving to a separate repo immediately

## Suggested Implementation Sequence

The intended order inside this mold is:

1. tighten docs and contract rails first
   - make sure the connector-facing scaffold docs say the same thing as the
     mold
2. extract shared runtime ownership
   - establish the neutral non-UI module before moving behavior around
3. split tenant vs connector-installation data ownership
   - introduce `tenant_connectors` before rewriting lots of callsites
4. introduce `ops` ownership clearly
   - move the control-plane story out of `public`
5. separate connector-owned canonical objects from non-connector objects
   - reduce ambiguity in the current `silver` layer/file ownership
6. only then sweep docs/tests touched by the new boundary

That order is recommended because each step reduces ambiguity for the next one.

## Common Mistakes To Avoid

- Do not smuggle Phase B work into this mold just because a rename feels tidy.
- Do not move code into `shared/` if it still contains Streamlit or app-only
  behavior.
- Do not treat `ops` as "random leftovers"; it is the connector control plane.
- Do not define the canonical contract narrowly; all downstream consumers should
  be able to rely on it.
- Do not keep Wati credentials on `public.tenants` just because it is the
  easiest place to reach from existing code.
- Do not leave classifier configuration sitting on the same admin surface just
  because it is convenient in the current app shell.
- Do not build a second half-finished connector UI when the CLI can solve the
  operator workflow more cleanly.

## Files to Modify
| File | Action | Purpose |
|------|--------|---------|
| `wati-to-supabase/README.md` | create | Product home and boundary description |
| `docs/PRODUCT_MAP.md` | refactor | Keep top-level product map aligned with connector work |
| `wati-to-supabase/contracts/canonical-contract.md` | create | Draft explicit connector-owned canonical contract |
| `wati-to-supabase/contracts/staging-semantics.md` | create | Draft message-event vs contact-snapshot staging semantics |
| `wati-to-supabase/contracts/tenant-connectors-model.md` | create | Draft tenant vs connector-installation ownership model |
| `wati-to-supabase/runtime/phase-a-boundary-cleanup.md` | create | Working notes and acceptance rails for Phase A |
| `wati-to-supabase/cli/README.md` | create | Connector CLI boundary, command posture, and watcher notes |
| `scripts/wati_supabase_cli.py` | create | Initial `wati-supabase` CLI scaffold and command tree |
| `wati-supabase` | create | Repo-root executable wrapper for the CLI |
| `scripts/wati_client.py` | refactor later | Connector-owned Wati API client |
| `scripts/batch_contact_imports.py` | refactor later | Connector-owned contact sync |
| `scripts/batch_messages_import.py` | refactor later | Connector-owned message backfill |
| `scripts/task_daemon.py` | refactor later | Connector-owned operational daemon |
| `scripts/daemon_tasks/` | refactor later | Drift/health/reconciliation tasks |
| `scripts/` CLI entry point or package scaffold | create later | Home for the `wati-supabase` command |
| `supabase/functions/wati-webhook/` | refactor later | Connector-owned webhook runtime |
| `app/database.py` or successor shared module | refactor later | Remove app-owned helper dependency |
| `app/pages/2_Admin.py` | refactor later | Remove connector control-plane ownership from shared admin page |
| `app/auth.py` | refactor later | Separate connector setup assumptions from shared app auth shell |
| `shared/` or successor neutral package | create later | Shared runtime/data access layer for both products |
| `shared/runtime/` | create | Seed neutral runtime package for extracted non-UI helpers |
| `database/01_tenants.sql` | refactor later | Separate tenant and connector config |
| `database/04_rls_policies.sql` | refactor later | Keep tenant isolation coherent after tenant/connector split |
| `database/02_bronze_layer.sql` | refactor later | Rename conceptual layer to staging and clarify raw ingress contract |
| `database/03_silver_layer.sql` | refactor later | Rename conceptual layer to canonical and define shared conversation contract |
| `database/05_functions.sql` | refactor later | Connector processing contract across staging -> canonical |
| `database/06_webhook_logging.sql` | refactor later | Connector observability contract |
| `database/07_gold_layer.sql` | refactor later | Rename conceptual layer to analytics for downstream views |
| `database/08_grants.sql` | refactor later | Grant/exposure updates for staging, canonical, analytics, and ops |
| `README.md` | refactor later | Replace medallion description with product/layer contract language |
| `scripts/BATCH_IMPORT_README.md` | refactor later | Connector operator docs and troubleshooting queries |
| `supabase/functions/wati-webhook/README.md` | refactor later | Webhook architecture and troubleshooting docs after rename |
| `tests/unit/test_app_database.py` | refactor later | Update schema expectations in shared DB helper tests |
| `tests/unit/test_sql_contracts.py` | refactor later | Update literal schema assertions |

## Verification

- Repo structure makes the connector product explicit
- Mold design identifies the runtime/module boundary clearly
- Mold design identifies the target layer vocabulary clearly and consistently
- Mold design locks the real-time runtime model: pure Supabase for webhook
  ingest, daemon for scheduled/repair work
- Mold design makes staging semantics explicit for both events and snapshots
- Mold design makes the canonical contract explicit as connector-owned surface
- Mold design defines `ops` retention/redaction direction for webhook data
- Mold design defines exact drift as low-frequency integrity checking, not core
  ingest
- Mold design defines the tenant vs connector-installation schema split
- Mold design defines neutral shared runtime module ownership
- Mold design defines identity refresh as permanent but non-destructive
- Mold design defines `ops` as a real schema target
- Mold design defines that `canonical` excludes non-connector tables
- Mold design defers physical rename and cutover to `dialo-8`
- Mold design is now scoped as a concrete Phase A build slice
- Future build should leave connector scripts runnable without importing
  Streamlit-specific modules
- Future build should document the Supabase contract the connector guarantees
- Future build should not require a coordinated schema-name deploy window
- Scaffold docs under `wati-to-supabase/` and `shared/runtime/` are aligned
  with the build plan rather than abandoned placeholders
- Future build should leave a connector admin surface that is CLI-first and
  does not also act as the classifier configuration page

## Implementation Notes

- Keep the product explicitly named `Wati -> Supabase`; that is clearer and more
  honest than pretending it is already a generic messaging connector.
- The naming decision should be treated as architectural vocabulary first.
  Physical rename is explicitly handled by `dialo-8`, not this mold.
- The largest hidden dependency discovered in design is that `silver` currently
  contains both connector and non-connector state. That must be resolved before
  the connector can honestly claim a clean `canonical` schema.
- `database/07_gold_layer.sql` being application-layer analytics, not connector
  functionality, is the second major scope limiter for a single-slice build.
- The scaffold docs are not optional fluff; they are the intended rails for the
  first extraction and contract commits in this product area.
- This mold should feel like onboarding material for the connector product.
  If a future contributor reads it and still cannot explain the product
  boundary, the mold is underspecified.
- The CLI name is intentionally simple: `wati-supabase`. Do not bikeshed this
  into a broader umbrella name before the product boundary is actually stable.
