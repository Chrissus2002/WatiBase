-- Legacy Wati-lookup normalizer: strips non-digits, then fixes Mexican mobile
-- numbers (52X → 521X). Mirrors the Python legacy_wati_lookup_phone() helper
-- so SQL joins and Python fetches never drift apart.
-- Lives in public so PostgREST can expose it via rpc if needed.
create or replace function public.normalize_phone(p_phone text)
returns text as $$
declare
  v_digits text;
begin
  if p_phone is null then
    return null;
  end if;

  -- Strip all non-digits
  v_digits := regexp_replace(p_phone, '[^0-9]', '', 'g');

  -- Fix Mexican mobile numbers: 52XXXXXXXXXX → 521XXXXXXXXXX
  -- If starts with '52' but third digit is not '1', insert '1' after '52'
  if left(v_digits, 2) = '52' and left(v_digits, 3) != '521' then
    v_digits := '521' || substring(v_digits from 3);
  end if;

  return v_digits;
end;
$$ language plpgsql immutable;


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
    phone_raw_primary,
    wati_lookup_phone,
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
    coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber') as phone_raw_primary,
    public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber')) as wati_lookup_phone,
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
    phone_raw_primary = coalesce(excluded.phone_raw_primary, canonical.contacts.phone_raw_primary),
    wati_lookup_phone = coalesce(excluded.wati_lookup_phone, canonical.contacts.wati_lookup_phone),
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
    provider_phone,
    is_primary,
    first_seen_at,
    last_seen_at
  )
  select
    c.contact_id,
    ctp.tenant_id,
    'wati',
    ctp.wati_contact_id,
    coalesce(ctp.raw_data->>'wAid', ctp.raw_data->>'phone', ctp.raw_data->>'whatsappNumber'),
    true,
    now(),
    now()
  from contacts_to_process ctp
  join canonical.contacts c
    on c.tenant_id = ctp.tenant_id
    and c.phone_normalized = public.normalize_phone(
      coalesce(ctp.raw_data->>'wAid', ctp.raw_data->>'phone', ctp.raw_data->>'whatsappNumber')
    )
  on conflict (tenant_id, provider, provider_contact_id)
  do update set
    provider_phone = coalesce(excluded.provider_phone, canonical.contact_provider_keys.provider_phone),
    last_seen_at = now(),
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


grant execute on all functions in schema public to service_role;

alter default privileges in schema public
    grant execute on functions to service_role;


-- Promote messages from staging.messages_raw_data into canonical.messages
-- for a given import batch. Resolves contact_id by joining to canonical.contacts.
-- Called via Supabase rpc('process_messages_to_canonical', {p_batch_id: '...'})
create or replace function public.process_messages_to_canonical(p_batch_id uuid)
returns jsonb as $$
declare
  v_tenant_id bigint;
  v_messages_processed integer := 0;
  v_messages_skipped integer := 0;
begin
  select distinct tenant_id into v_tenant_id
  from staging.messages_raw_data
  where batch_id = p_batch_id
  limit 1;

  if v_tenant_id is null then
    raise exception 'No messages found for batch_id: %', p_batch_id;
  end if;

  raise notice 'Processing message batch % for tenant %', p_batch_id, v_tenant_id;

  -- Insert messages that have a matching canonical contact.
  -- Join on phone_normalized derived from conversation_id (the phone used to fetch).
  -- Skip messages where no canonical contact exists.
  with messages_to_process as (
    select
      s.tenant_id,
      s.wati_message_id,
      s.conversation_id,
      s.raw_data,
      c.contact_id
    from staging.messages_raw_data s
    join canonical.contacts c
      on c.tenant_id = s.tenant_id
      and coalesce(c.wati_lookup_phone, c.phone_normalized) = public.normalize_phone(s.conversation_id)
    where s.batch_id = p_batch_id
  )
  insert into canonical.messages (
    tenant_id,
    contact_id,
    batch_id,
    conversation_id,
    message_content,
    direction,
    message_type,
    wati_message_id,
    timestamp
  )
  select
    tenant_id,
    contact_id,
    p_batch_id,
    conversation_id,
    coalesce(raw_data->>'text', raw_data->>'body') as message_content,
    raw_data->>'type' as direction,
    coalesce(raw_data->>'messageType', 'text') as message_type,
    wati_message_id,
    coalesce(
      case
        when coalesce(raw_data->>'timestamp', '') ~ '^[0-9]+(\.[0-9]+)?$'
          then to_timestamp((raw_data->>'timestamp')::double precision)
        when raw_data ? 'timestamp'
          then (raw_data->>'timestamp')::timestamptz
        else null
      end,
      case
        when coalesce(raw_data->>'created', '') ~ '^[0-9]+(\.[0-9]+)?$'
          then to_timestamp((raw_data->>'created')::double precision)
        when raw_data ? 'created'
          then (raw_data->>'created')::timestamptz
        else null
      end,
      now()
    ) as timestamp
  from messages_to_process
  on conflict (tenant_id, wati_message_id) do nothing;

  get diagnostics v_messages_processed = row_count;

  -- Count how many staged messages had no matching contact
  select count(*) into v_messages_skipped
  from staging.messages_raw_data s
  where s.batch_id = p_batch_id
    and not exists (
      select 1 from canonical.contacts c
      where c.tenant_id = s.tenant_id
        and coalesce(c.wati_lookup_phone, c.phone_normalized) = public.normalize_phone(s.conversation_id)
    );

  raise notice 'Complete: % messages processed, % skipped (no matching contact)',
    v_messages_processed, v_messages_skipped;

  return jsonb_build_object(
    'batch_id', p_batch_id,
    'tenant_id', v_tenant_id,
    'messages_processed', v_messages_processed,
    'messages_skipped', v_messages_skipped
  );
end;
$$ language plpgsql;
