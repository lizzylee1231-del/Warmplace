-- Run after the migration INSIDE a transaction; roll back the whole rehearsal.
-- Fixed negative IDs avoid advancing real identity sequences.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"4ea7493f-b5ef-4a13-9a08-69190eb2d691","role":"authenticated"}', true);
insert into public.mood_records (id,user_id,mood_text,intensity,scene_category,client_request_id)
values (-424242424,'4ea7493f-b5ef-4a13-9a08-69190eb2d691','synthetic rollback-only check',3,'test','9a39965d-a586-4685-9097-899827a10f67');
insert into public.user_profiles (id,user_id,profile_text)
values (-424242424,'4ea7493f-b5ef-4a13-9a08-69190eb2d691','synthetic rollback-only check');
do $$ begin
  if (select count(*) from public.mood_records where id=-424242424) <> 1 then raise exception 'owner cannot read record'; end if;
  if (select count(*) from public.user_profiles where id=-424242424) <> 1 then raise exception 'owner cannot read profile'; end if;
end $$;

-- DO NOTHING must resolve duplicate request IDs without overwriting old content.
insert into public.mood_records (id,user_id,mood_text,intensity,scene_category,client_request_id)
values (-424242425,'4ea7493f-b5ef-4a13-9a08-69190eb2d691','duplicate',3,'test','9a39965d-a586-4685-9097-899827a10f67')
on conflict (user_id,client_request_id) do nothing;
do $$ begin
  if (select count(*) from public.mood_records where id in (-424242424,-424242425)) <> 1 then raise exception 'duplicate request created a record'; end if;
  begin
    update public.mood_records set user_id='ac312fe4-a333-4ffb-8098-0aad55a394a0' where id=-424242424;
    raise exception 'ownership reassignment was permitted';
  exception when insufficient_privilege then null;
  end;
end $$;

select set_config('request.jwt.claims', '{"sub":"ac312fe4-a333-4ffb-8098-0aad55a394a0","role":"authenticated"}', true);
do $$ declare touched integer; begin
  if exists(select 1 from public.mood_records where id=-424242424) then raise exception 'foreign record visible'; end if;
  if exists(select 1 from public.user_profiles where id=-424242424) then raise exception 'foreign profile visible'; end if;
  update public.mood_records set is_deleted=true where id=-424242424;
  get diagnostics touched = row_count;
  if touched <> 0 then raise exception 'foreign soft delete permitted'; end if;
  begin
    insert into public.user_profiles (id,user_id,profile_text)
    values (-424242425,'4ea7493f-b5ef-4a13-9a08-69190eb2d691','forbidden');
    raise exception 'foreign profile insert permitted';
  exception when insufficient_privilege then null;
  end;
end $$;

reset role;
set local role anon;
do $$ begin
  begin
    perform 1 from public.mood_records limit 1;
    raise exception 'unauthenticated table access permitted';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;
select 'owner/foreign/anonymous/idempotency checks passed; transaction will roll back' as result;
