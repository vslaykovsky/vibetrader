alter table public.profiles
  add column if not exists timezone text not null default '';
