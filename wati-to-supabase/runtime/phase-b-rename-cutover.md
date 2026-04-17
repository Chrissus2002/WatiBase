# Phase B Rename And Cutover

Working notes for `dialo-8`.

## Scope

Phase B is the physical rename and deploy cutover:

- `bronze` -> `staging`
- `silver` -> `canonical`
- `gold` -> `analytics`
- connector control-plane objects -> `ops`

## Required Coordination

- SQL object rename/recreation
- code callsite sweep
- grants and exposed-schema updates
- docs and tests updated in the same branch
- one deliberate deploy/cutover sequence

## Verification Targets

- webhook ingest still works
- batch contact import still works
- batch message backfill still works
- daemon tasks still work
- self-hosted access still works

## Rule

Do not let Phase B reopen product-boundary questions already settled in
`dialo-6`.
