# Shared Runtime

Neutral home for non-UI runtime and data-access code shared across the connector
and application layer.

## Intended First Moves

Phase A connector cleanup is expected to start moving code here from:

- `app/database.py`
- script-local Supabase client setup
- any connector/runtime helper that should not live in Streamlit modules

## Guardrails

- no Streamlit caching or UI imports
- no Wati-only business logic here
- no application UX code here

If it is runtime-oriented and not Wati-specific, it probably belongs here.
