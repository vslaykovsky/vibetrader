alter table public.profiles
  add column if not exists hour_format text not null default 'auto'
  check (hour_format in ('auto', '12h', '24h'));
