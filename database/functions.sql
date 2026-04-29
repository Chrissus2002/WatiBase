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

  -- Deduplicate within the batch by normalized phone (latest last_seen_at wins).
  -- Resolve existing provider identity before falling back to normalized phone.
  with contacts_to_process as (
    select distinct on (
      tenant_id,
      public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber'))
    )
      tenant_id,
      wati_contact_id,
      raw_data,
      public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber')) as normalized_phone,
      coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber') as raw_phone,
      coalesce(raw_data->>'fullName', raw_data->>'name') as contact_name
    from staging.contacts_raw_data
    where batch_id = p_batch_id
      and coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber') is not null
    order by
      tenant_id,
      public.normalize_phone(coalesce(raw_data->>'wAid', raw_data->>'phone', raw_data->>'whatsappNumber')),
      last_seen_at desc
  ),
  resolved as (
    select
      ctp.*,
      cpk.contact_id as provider_contact_match_id,
      c_by_phone.contact_id as phone_contact_match_id,
      coalesce(cpk.contact_id, c_by_phone.contact_id) as resolved_contact_id
    from contacts_to_process ctp
    left join canonical.contact_provider_keys cpk
      on cpk.tenant_id = ctp.tenant_id
      and cpk.provider = 'wati'
      and cpk.provider_contact_id = ctp.wati_contact_id
    left join canonical.contacts c_by_phone
      on c_by_phone.tenant_id = ctp.tenant_id
      and c_by_phone.phone_normalized = ctp.normalized_phone
  ),
  updated as (
    update canonical.contacts c
    set
      batch_id = p_batch_id,
      phone_normalized = case
        when resolved.normalized_phone is null then c.phone_normalized
        when c.phone_normalized = resolved.normalized_phone then c.phone_normalized
        when exists (
          select 1
          from canonical.contacts other
          where other.tenant_id = c.tenant_id
            and other.phone_normalized = resolved.normalized_phone
            and other.contact_id <> c.contact_id
        ) then c.phone_normalized
        else resolved.normalized_phone
      end,
      phone_raw = coalesce(resolved.raw_phone, c.phone_raw),
      phone_raw_primary = coalesce(resolved.raw_phone, c.phone_raw_primary),
      wati_lookup_phone = coalesce(resolved.normalized_phone, c.wati_lookup_phone),
      name = case
        when resolved.contact_name is not null and (
          c.name is null or
          length(resolved.contact_name) > length(c.name)
        ) then resolved.contact_name
        else c.name
      end,
      wati_contact_id = coalesce(resolved.wati_contact_id, c.wati_contact_id),
      source = coalesce(c.source, 'wati'),
      updated_at = now()
    from resolved
    where resolved.resolved_contact_id = c.contact_id
    returning c.contact_id
  ),
  inserted as (
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
      resolved.tenant_id,
      p_batch_id,
      resolved.normalized_phone,
      resolved.raw_phone,
      resolved.raw_phone,
      resolved.normalized_phone,
      resolved.contact_name,
      resolved.wati_contact_id,
      'wati',
      true
    from resolved
    where resolved.resolved_contact_id is null
    returning contact_id, tenant_id, phone_normalized
  ),
  provider_rows as (
    select
      coalesce(resolved.resolved_contact_id, inserted.contact_id) as contact_id,
      resolved.tenant_id,
      resolved.wati_contact_id,
      resolved.raw_phone
    from resolved
    left join inserted
      on inserted.tenant_id = resolved.tenant_id
      and inserted.phone_normalized = resolved.normalized_phone
  ),
  provider_upsert as (
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
      provider_rows.contact_id,
      provider_rows.tenant_id,
      'wati',
      provider_rows.wati_contact_id,
      provider_rows.raw_phone,
      true,
      now(),
      now()
    from provider_rows
    where provider_rows.contact_id is not null
      and provider_rows.wati_contact_id is not null
    on conflict (tenant_id, provider, provider_contact_id)
    do update set
      contact_id = excluded.contact_id,
      provider_phone = coalesce(excluded.provider_phone, canonical.contact_provider_keys.provider_phone),
      is_primary = excluded.is_primary,
      last_seen_at = excluded.last_seen_at,
      updated_at = now()
    returning contact_id
  )
  select
    (select count(*) from updated) + (select count(*) from inserted),
    (select count(*) from inserted)
  into v_contacts_processed, v_contacts_created;

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


-- Promote a single webhook-delivered message from staging into canonical.
-- Looks up the staging row by its primary key, resolves the canonical contact,
-- and inserts into canonical.messages. Returns a summary JSON with the outcome.
-- Called from the wati-webhook edge function after staging the raw payload.
create or replace function public.process_webhook_message_to_canonical(p_staging_id uuid)
returns jsonb as $$
declare
  v_row staging.messages_raw_data%rowtype;
  v_contact_id uuid;
  v_rows_inserted integer := 0;
begin
  select * into v_row
  from staging.messages_raw_data
  where id = p_staging_id;

  if v_row.id is null then
    return jsonb_build_object(
      'status', 'error',
      'error', format('Staging row %s not found', p_staging_id)
    );
  end if;

  -- Resolve canonical contact via normalized phone
  select contact_id into v_contact_id
  from canonical.contacts
  where tenant_id = v_row.tenant_id
    and coalesce(wati_lookup_phone, phone_normalized) = public.normalize_phone(v_row.conversation_id)
  limit 1;

  if v_contact_id is null then
    return jsonb_build_object(
      'status', 'skipped',
      'reason', 'no_matching_contact',
      'staging_id', p_staging_id,
      'tenant_id', v_row.tenant_id,
      'conversation_id', v_row.conversation_id
    );
  end if;

  -- Insert into canonical, skip on duplicate
  insert into canonical.messages (
    tenant_id,
    contact_id,
    conversation_id,
    message_content,
    direction,
    message_type,
    wati_message_id,
    timestamp
  )
  values (
    v_row.tenant_id,
    v_contact_id,
    v_row.conversation_id,
    coalesce(v_row.raw_data->>'text', v_row.raw_data->>'body'),
    v_row.raw_data->>'type',
    coalesce(v_row.raw_data->>'messageType', 'text'),
    v_row.wati_message_id,
    coalesce(
      case
        when coalesce(v_row.raw_data->>'timestamp', '') ~ '^[0-9]+(\.[0-9]+)?$'
          then to_timestamp((v_row.raw_data->>'timestamp')::double precision)
        when v_row.raw_data ? 'timestamp'
          then (v_row.raw_data->>'timestamp')::timestamptz
        else null
      end,
      case
        when coalesce(v_row.raw_data->>'created', '') ~ '^[0-9]+(\.[0-9]+)?$'
          then to_timestamp((v_row.raw_data->>'created')::double precision)
        when v_row.raw_data ? 'created'
          then (v_row.raw_data->>'created')::timestamptz
        else null
      end,
      now()
    )
  )
  on conflict (tenant_id, wati_message_id) do nothing;

  get diagnostics v_rows_inserted = row_count;

  return jsonb_build_object(
    'status', case when v_rows_inserted > 0 then 'success' else 'duplicate' end,
    'staging_id', p_staging_id,
    'tenant_id', v_row.tenant_id,
    'contact_id', v_contact_id,
    'wati_message_id', v_row.wati_message_id
  );
end;
$$ language plpgsql;


-- Update the per-tenant webhook health aggregates in ops.webhook_processing_status.
-- Increments counters atomically. Called from the wati-webhook edge function.
create or replace function public.update_webhook_status(
  p_tenant_id bigint,
  p_status text  -- 'success', 'skipped', or 'failed'
)
returns void as $$
begin
  insert into ops.webhook_processing_status (
    tenant_id,
    last_received_at,
    last_processed_at,
    last_status,
    total_received,
    total_succeeded,
    total_failed,
    updated_at
  )
  values (
    p_tenant_id,
    now(),
    now(),
    p_status,
    1,
    case when p_status in ('success', 'skipped') then 1 else 0 end,
    case when p_status = 'failed' then 1 else 0 end,
    now()
  )
  on conflict (tenant_id)
  do update set
    last_received_at = now(),
    last_processed_at = now(),
    last_status = excluded.last_status,
    total_received = ops.webhook_processing_status.total_received + 1,
    total_succeeded = ops.webhook_processing_status.total_succeeded
      + case when p_status in ('success', 'skipped') then 1 else 0 end,
    total_failed = ops.webhook_processing_status.total_failed
      + case when p_status = 'failed' then 1 else 0 end,
    updated_at = now();
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
