-- Drop everything from init_db.sql in reverse dependency order

-- Canonical
drop table if exists canonical.messages cascade;
drop table if exists canonical.contact_provider_keys cascade;
drop table if exists canonical.contacts cascade;

-- Staging
drop table if exists staging.messages_raw_data cascade;
drop table if exists staging.contacts_raw_data cascade;

-- Ops
drop table if exists ops.webhook_processing_status cascade;
drop table if exists ops.webhook_processing_log cascade;
drop table if exists ops.webhook_logs cascade;
drop table if exists ops.daemon_task_runs cascade;
drop table if exists ops.import_jobs cascade;

-- Public
drop table if exists public.tenant_connectors cascade;
drop table if exists public.tenant cascade;

-- Functions
drop function if exists public.process_messages_to_canonical(uuid);
drop function if exists public.process_contacts_to_canonical(uuid);
drop function if exists public.normalize_phone(text);

-- Schemas
drop schema if exists analytics cascade;
drop schema if exists canonical cascade;
drop schema if exists staging cascade;
drop schema if exists ops cascade;
