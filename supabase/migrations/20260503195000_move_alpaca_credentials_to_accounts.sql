alter table public.alpaca_accounts
  add column if not exists alpaca_api_key text not null default '',
  add column if not exists alpaca_secret_key text not null default '';

update public.alpaca_accounts a
set
  alpaca_api_key = coalesce(nullif(a.alpaca_api_key, ''), p.alpaca_api_key, ''),
  alpaca_secret_key = coalesce(nullif(a.alpaca_secret_key, ''), p.alpaca_secret_key, ''),
  updated_at = now()
from public.profiles p
where a.user_id = p.id
  and (
    coalesce(a.alpaca_api_key, '') = ''
    or coalesce(a.alpaca_secret_key, '') = ''
  );

alter table public.profiles
  drop column if exists alpaca_api_key,
  drop column if exists alpaca_secret_key;

alter table public.alpaca_accounts
  drop column if exists account;
