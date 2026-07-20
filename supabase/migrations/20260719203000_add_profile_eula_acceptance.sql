alter table public.profiles
  add column if not exists eula_accepted boolean not null default false,
  add column if not exists eula_version text not null default '',
  add column if not exists eula_accepted_at timestamptz,
  add column if not exists eula_accepted_ip inet,
  add column if not exists eula_accepted_user_agent text not null default '';

alter table public.profiles
  drop constraint if exists profiles_eula_acceptance_complete;

alter table public.profiles
  add constraint profiles_eula_acceptance_complete check (
    not eula_accepted
    or (eula_version <> '' and eula_accepted_at is not null)
  );

-- Acceptance is recorded only by the backend service role. Authenticated users
-- retain their existing read access but receive no write grant for these fields.
revoke insert (eula_accepted, eula_version, eula_accepted_at, eula_accepted_ip, eula_accepted_user_agent)
  on public.profiles from authenticated;
revoke update (eula_accepted, eula_version, eula_accepted_at, eula_accepted_ip, eula_accepted_user_agent)
  on public.profiles from authenticated;
