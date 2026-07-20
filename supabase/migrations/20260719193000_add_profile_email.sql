alter table public.profiles
  add column if not exists email text not null default '';

update public.profiles p
set email = coalesce(u.email, '')
from auth.users u
where p.id = u.id
  and p.email is distinct from coalesce(u.email, '');

create or replace function public.handle_new_user_profiles()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, coalesce(new.email, ''));
  return new;
end;
$$;

create or replace function public.handle_user_email_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.profiles
  set email = coalesce(new.email, ''), updated_at = now()
  where id = new.id;
  return new;
end;
$$;

drop trigger if exists on_auth_user_email_changed_profiles on auth.users;
create trigger on_auth_user_email_changed_profiles
  after update of email on auth.users
  for each row
  when (old.email is distinct from new.email)
  execute function public.handle_user_email_change();
