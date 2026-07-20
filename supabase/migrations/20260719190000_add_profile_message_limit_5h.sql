alter table public.profiles
  add column if not exists message_limit_5h integer not null default 5
  check (message_limit_5h >= 1);

-- RLS limits rows, not columns. Keep users from raising their own allowance
-- through the Supabase data API while preserving access to display preferences.
revoke insert, update on table public.profiles from authenticated;
grant insert (id, timezone, hour_format, adjust_for_dividends, interface_language, updated_at)
  on table public.profiles to authenticated;
grant update (timezone, hour_format, adjust_for_dividends, interface_language, updated_at)
  on table public.profiles to authenticated;
