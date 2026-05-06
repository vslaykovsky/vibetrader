alter table public.profiles
  add column if not exists adjust_for_dividends boolean not null default false;
