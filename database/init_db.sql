create schema if not exists staging;
create schema if not exists canonical;
create schema if not exists analytics;
create schema if not exists ops;


-- PUBLIC — workspace identity and connector config
-- Workspace identity only. No connector credentials here.
create table if not exists public.tenant (
    tenant_id bigint generated always as identity primary key,
    name text not null,
    slug text not null unique,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Wati connector installation state, separate from workspace identity.
-- One active Wati install per tenant enforced by partial unique index below.
create table if not exists public.tenant_connectors (
    connector_id uuid not null default gen_random_uuid() primary key,
    tenant_id bigint not null references public.tenant(tenant_id),
    wati_account_id text,
    wati_api_token text,
    is_active boolean not null default true,
    last_successful_sync_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,
    updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- At most one active Wati connector per tenant
create unique index if not exists uq_tenant_connectors_active_wati
    on public.tenant_connectors (tenant_id, provider)
    where is_active = true;



-- STAGING
-- Contacts are latest-seen snapshots, safe to upsert.
create table if not exists staging.contacts_raw_data (
    id uuid not null default gen_random_uuid() primary key,
    tenant_id bigint not null references public.tenant(tenant_id),
    batch_id uuid,
    wati_contact_id text not null,
    raw_data jsonb not null,
    last_seen_at timestamp with time zone default timezone('utc'::text, now()) not null,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,

    constraint uq_staging_contact_per_tenant unique (tenant_id, wati_contact_id)
);

-- Messages are an event log — append-only, immutable once captured.
create table if not exists staging.messages_raw_data (
    id uuid not null default gen_random_uuid() primary key,
    tenant_id bigint not null references public.tenant(tenant_id),
    batch_id uuid,
    wati_message_id text not null,
    conversation_id text not null,
    raw_data jsonb not null,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,

    constraint uq_staging_message_per_tenant unique (tenant_id, wati_message_id)
);



-- CANONICAL (silver) — normalized conversation contract
-- One row per canonical contact identity. Provider-agnostic.
create table if not exists canonical.contacts (
      contact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id BIGINT NOT NULL REFERENCES public.tenants(tenant_id),
  batch_id UUID REFERENCES public.import_jobs(job_id),  -- NULL for webhooks

  -- Phone identity (canonical key for deduplication)
  phone_normalized TEXT NOT NULL, -- e.g., "5215554146581"
  phone_raw TEXT, -- exactly as received from Wati

  -- Profile
  name TEXT,
  -- Source where the contact came from
  source TEXT,
  wati_contact_id TEXT,

  -- Merge tracking
  is_canonical BOOLEAN DEFAULT true,

  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(tenant_id, phone_normalized)
);

create index if not exists idx_canonical_contacts_tenant
    on canonical.contacts (tenant_id);

-- Maps Wati provider identities to canonical contacts.
-- Provider-specific fields live here, not on canonical.contacts.
create table if not exists canonical.contact_provider_keys (
    id uuid not null default gen_random_uuid() primary key,
    contact_id uuid not null references canonical.contacts(contact_id),
    tenant_id bigint not null references public.tenant(tenant_id),
    provider text not null default 'wati',
    provider_contact_id text not null,
    provider_phone text,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,
    updated_at timestamp with time zone default timezone('utc'::text, now()) not null,

    constraint uq_provider_contact_per_tenant unique (tenant_id, provider, provider_contact_id)
);

create index if not exists idx_contact_provider_keys_contact
    on canonical.contact_provider_keys (contact_id);

-- One row per conversational unit. direction and timestamp are connector-guaranteed.
create table if not exists canonical.messages (
  message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id BIGINT NOT NULL REFERENCES public.tenants(tenant_id),
  contact_id UUID NOT NULL REFERENCES silver.contacts(contact_id),
  batch_id UUID REFERENCES public.import_jobs(job_id),  -- NULL for webhooks
  conversation_id text,

  -- Message content
  message_content TEXT,
  direction TEXT, -- 'inbound', 'outbound'
  message_type TEXT, -- 'text', 'image', 'template'

  -- External reference
  wati_message_id TEXT,

  -- Timestamps
  timestamp TIMESTAMPTZ NOT NULL, -- when message was sent/received
  created_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(tenant_id, wati_message_id) -- prevent duplicate webhook processing
);

create index if not exists idx_canonical_messages_contact
    on canonical.messages (contact_id, timestamp);

create index if not exists idx_canonical_messages_tenant
    on canonical.messages (tenant_id, timestamp);

-- ============================================================
-- OPS — connector control plane
-- ============================================================

create table if not exists ops.import_jobs (
    job_id uuid not null default gen_random_uuid() primary key,
    tenant_id bigint not null references public.tenant(tenant_id),
    job_type text not null, -- 'contact_import', 'message_backfill', etc.
    status text not null default 'pending'
        check (status in ('pending', 'running', 'completed', 'failed')),
    processed_count bigint not null default 0,
    total_count bigint,
    subprocess_pid int,
    error_message text,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,
    started_at timestamp with time zone,
    completed_at timestamp with time zone
);

create index if not exists idx_import_jobs_tenant_status
    on ops.import_jobs (tenant_id, status);

create table if not exists ops.daemon_task_runs (
    run_id uuid not null default gen_random_uuid() primary key,
    tenant_id bigint not null references public.tenant(tenant_id),
    task_name text not null,
    status text not null default 'running'
        check (status in ('running', 'completed', 'failed')),
    result jsonb,
    error_message text,
    notes text,
    started_at timestamp with time zone default timezone('utc'::text, now()) not null,
    finished_at timestamp with time zone
);

create index if not exists idx_daemon_task_runs_tenant_task
    on ops.daemon_task_runs (tenant_id, task_name, started_at desc);

-- Raw webhook receipts for replay/debug. Auth headers are never stored.
-- Rows expire after 30 days (enforce via pg_cron or application-level cleanup).
create table if not exists ops.webhook_logs (
    log_id uuid not null default gen_random_uuid() primary key,
    tenant_id bigint references public.tenant(tenant_id),
    received_at timestamp with time zone default timezone('utc'::text, now()) not null,
    event_type text,
    payload jsonb,
    processed boolean not null default false,
    error text,
    expires_at timestamp with time zone default (timezone('utc'::text, now()) + interval '30 days') not null
);

create index if not exists idx_webhook_logs_tenant_received
    on ops.webhook_logs (tenant_id, received_at desc);

create index if not exists idx_webhook_logs_expires
    on ops.webhook_logs (expires_at);
