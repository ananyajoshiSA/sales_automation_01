-- Run once in Supabase: SQL Editor > New query > paste > Run.
-- Change the admin email below to the address you will log in with.
create table public.results (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(name) between 3 and 120),
  email text not null check (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  org text,
  dept text not null,
  pos text not null,
  type text not null check (type ~ '^[EI][SN][TF][JP]$'),
  pct jsonb not null,
  answers jsonb,
  secs int,
  taken_at timestamptz default now(),
  v int default 5
);
create unique index results_email_uq on public.results (lower(email));

alter table public.results enable row level security;

-- Anyone with the link can submit one result, but cannot read anyone's.
create policy "anyone can submit" on public.results for insert to anon with check (true);

-- Only the admin can read or reset results.
create policy "admin reads" on public.results for select to authenticated
  using ((auth.jwt() ->> 'email') = 'admin@lawsikho.in');
create policy "admin deletes" on public.results for delete to authenticated
  using ((auth.jwt() ->> 'email') = 'admin@lawsikho.in');

-- Lets the form say "already taken" before someone spends 10 minutes (returns true/false only).
create function public.has_taken(p_email text) returns boolean
  language sql security definer set search_path = public as
  $$ select exists (select 1 from public.results where lower(email) = lower(p_email)) $$;
grant execute on function public.has_taken(text) to anon, authenticated;
