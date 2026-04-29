create extension if not exists "pgcrypto";

create table if not exists public.profiles (
  id uuid not null primary key references auth.users (id) on delete cascade,
  alpaca_api_key text not null default '',
  alpaca_secret_key text not null default '',
  updated_at timestamptz not null default now()
);

create table if not exists public.alpaca_accounts (
  id uuid not null primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  account text not null default '',
  label text not null default '',
  is_live boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists alpaca_accounts_user_id_idx on public.alpaca_accounts (user_id);

alter table public.profiles enable row level security;
alter table public.alpaca_accounts enable row level security;

create policy "profiles_select_own"
  on public.profiles for select
  using (auth.uid() = id);

create policy "profiles_insert_own"
  on public.profiles for insert
  with check (auth.uid() = id);

create policy "profiles_update_own"
  on public.profiles for update
  using (auth.uid() = id);

create policy "alpaca_accounts_select_own"
  on public.alpaca_accounts for select
  using (auth.uid() = user_id);

create policy "alpaca_accounts_insert_own"
  on public.alpaca_accounts for insert
  with check (auth.uid() = user_id);

create policy "alpaca_accounts_update_own"
  on public.alpaca_accounts for update
  using (auth.uid() = user_id);

create policy "alpaca_accounts_delete_own"
  on public.alpaca_accounts for delete
  using (auth.uid() = user_id);

create or replace function public.handle_new_user_profiles()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id) values (new.id);
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_profiles on auth.users;
create trigger on_auth_user_created_profiles
  after insert on auth.users
  for each row execute function public.handle_new_user_profiles();

insert into public.profiles (id)
select u.id
from auth.users u
where not exists (select 1 from public.profiles p where p.id = u.id);

grant usage on schema public to postgres, anon, authenticated, service_role;
grant all on table public.profiles to postgres, service_role;
grant all on table public.alpaca_accounts to postgres, service_role;
grant select, insert, update, delete on table public.profiles to authenticated;
grant select, insert, update, delete on table public.alpaca_accounts to authenticated;
