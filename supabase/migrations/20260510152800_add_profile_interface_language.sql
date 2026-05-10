alter table public.profiles
  add column if not exists interface_language text not null default ''
  check (interface_language in ('', 'en', 'ru'));
