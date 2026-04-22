-- Helper: strip all non-digit characters from a phone string
-- Lives in public so PostgREST can expose it via rpc if needed.
create or replace function public.normalize_phone(p_phone text)
returns text as $$
  select regexp_replace(p_phone, '\D', '', 'g');
$$ language sql immutable;


-- Promote contacts from staging.contacts_raw_data into canonical.contacts
-- and canonical.contact_provider_keys for a given import batch.
-- Called via Supabase rpc('process_contacts_to_canonical', {p_batch_id: '...'})
create or replace function public.process_contacts_to_canonical(p_batch_id uuid)
returns jsonb as $$
declare
  v_tenant_id bigint;
  v_contacts_processed integer := 0;
  v_contacts_created integer := 0;
  v_contacts_updated integer := 0;
begin
  select distinct tenant_id into v_tenant_id
  from staging.contacts_raw_data
  where batch_id = p_batch_id
  limit 1;

  if v_tenant_id is null then
    raise exception 'No contacts found for batch_id: %', p_batch_id;
  end if;

  raise notice 'Processing batch % for tenant %', p_batch_id, v_tenant_id;

  -- Deduplicate within the batch by normalized phone (latest last_seen_at wins)
  with contacts_to_process as (
    select distinct on (
      tenant_id,
      public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber'))
    )
      tenant_id,
      wati_contact_id,
      raw_data
    from staging.contacts_raw_data
    where batch_id = p_batch_id
      and coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber') is not null
    order by
      tenant_id,
      public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber')),
      last_seen_at desc
  )
  insert into canonical.contacts (
    tenant_id,
    batch_id,
    phone_normalized,
    phone_raw,
    name,
    wati_contact_id,
    source,
    is_canonical
  )
  select
    tenant_id,
    p_batch_id,
    public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber')) as phone_normalized,
    coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber') as phone_raw,
    coalesce(raw_data->>'fullName', raw_data->>'name') as name,
    wati_contact_id,
    'wati' as source,
    true as is_canonical
  from contacts_to_process
  on conflict (tenant_id, phone_normalized)
  do update set
    name = case
      when excluded.name is not null and (
        canonical.contacts.name is null or
        length(excluded.name) > length(canonical.contacts.name)
      ) then excluded.name
      else canonical.contacts.name
    end,
    wati_contact_id = coalesce(excluded.wati_contact_id, canonical.contacts.wati_contact_id),
    phone_raw = coalesce(excluded.phone_raw, canonical.contacts.phone_raw),
    updated_at = now();

  get diagnostics v_contacts_processed = row_count;

  -- Upsert provider keys, joining back to get the canonical contact_id
  with contacts_to_process as (
    select distinct on (
      tenant_id,
      public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber'))
    )
      tenant_id,
      wati_contact_id,
      raw_data
    from staging.contacts_raw_data
    where batch_id = p_batch_id
      and coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber') is not null
    order by
      tenant_id,
      public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber')),
      last_seen_at desc
  )
  insert into canonical.contact_provider_keys (
    contact_id,
    tenant_id,
    provider,
    provider_contact_id,
    provider_phone
  )
  select
    c.contact_id,
    ctp.tenant_id,
    'wati',
    ctp.wati_contact_id,
    coalesce(ctp.raw_data->>'wAid', ctp.raw_data->>'phone', ctp.raw_data->>'whatsappNumber')
  from contacts_to_process ctp
  join canonical.contacts c
    on c.tenant_id = ctp.tenant_id
    and c.phone_normalized = public.normalize_phone(
      coalesce(ctp.raw_data->>'wAid', ctp.raw_data->>'phone', ctp.raw_data->>'whatsappNumber')
    )
  on conflict (tenant_id, provider, provider_contact_id)
  do update set
    provider_phone = coalesce(excluded.provider_phone, canonical.contact_provider_keys.provider_phone),
    updated_at = now();

  select count(*) into v_contacts_created
  from canonical.contacts
  where tenant_id = v_tenant_id
    and created_at >= now() - interval '1 minute';

  v_contacts_updated := v_contacts_processed - v_contacts_created;

  raise notice 'Complete: % contacts (% created, % updated)',
    v_contacts_processed, v_contacts_created, v_contacts_updated;

  return jsonb_build_object(
    'batch_id', p_batch_id,
    'tenant_id', v_tenant_id,
    'contacts_processed', v_contacts_processed,
    'contacts_created', v_contacts_created,
    'contacts_updated', v_contacts_updated
  );
end;
$$ language plpgsql;
