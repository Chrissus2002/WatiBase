alter table public.tenant_connectors
    add column if not exists default_phone_region text;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'tenant_connectors_default_phone_region_check'
    ) then
        alter table public.tenant_connectors
            add constraint tenant_connectors_default_phone_region_check
            check (default_phone_region ~ '^[A-Z]{2}$');
    end if;
end
$$;

alter table canonical.contacts
    add column if not exists phone_raw_primary text,
    add column if not exists phone_identity_key text,
    add column if not exists phone_e164 text,
    add column if not exists normalization_status text,
    add column if not exists normalization_region text,
    add column if not exists wati_lookup_phone text;

create index if not exists idx_canonical_contacts_identity_key
    on canonical.contacts (tenant_id, phone_identity_key)
    where phone_identity_key is not null;

create index if not exists idx_canonical_contacts_e164
    on canonical.contacts (tenant_id, phone_e164)
    where phone_e164 is not null;

create index if not exists idx_canonical_contacts_wati_lookup
    on canonical.contacts (tenant_id, wati_lookup_phone)
    where wati_lookup_phone is not null;

create index if not exists idx_canonical_contacts_wati_contact_id
    on canonical.contacts (tenant_id, wati_contact_id)
    where wati_contact_id is not null;

alter table canonical.contact_provider_keys
    add column if not exists is_primary boolean not null default true,
    add column if not exists first_seen_at timestamp with time zone default timezone('utc'::text, now()) not null,
    add column if not exists last_seen_at timestamp with time zone default timezone('utc'::text, now()) not null;
