-- eBay keyword research store (Supabase/Postgres).
--
-- Design notes
--  * The queue lives here, not in the repo: the workflow asks what is due, so an
--    empty dispatch input is normal and keywords never pass through CI logs.
--  * `eb_listings` holds current state AND the previous observation, so the board
--    gets units/price deltas without a window function over observations.
--  * `eb_observations` is append-only history, deduped per (eb_id, run_id) and
--    pruned by eb_prune() to keep the free 500 MB tier comfortable.
--  * Only the Active Surface is collected (ADR 0001): no sold ledger, no login.

create extension if not exists pgcrypto with schema extensions;

-- ─────────────────────────────────── queue ───────────────────────────────────
create table if not exists public.eb_keywords (
    id            bigserial primary key,
    keyword       text        not null unique,
    depth         int         not null default 10 check (depth between 1 and 40),
    next_page     int         not null default 1,
    pass_no       int         not null default 1,
    status        text        not null default 'queued'
                              check (status in ('queued','running','paused','blocked')),
    priority      int         not null default 0,
    interval_hours int        not null default 48 check (interval_hours between 6 and 720),
    due_at        timestamptz not null default now(),
    added_by      text,
    added_at      timestamptz not null default now(),
    last_run_at   timestamptz,
    pages_done    int         not null default 0,
    passes_done   int         not null default 0,
    listings_seen int         not null default 0
);
create index if not exists eb_keywords_due_idx
    on public.eb_keywords (status, due_at, priority desc);

-- ────────────────────────────────── listings ─────────────────────────────────
create table if not exists public.eb_listings (
    eb_id          text primary key,
    url            text not null,
    title          text,
    seller         text,
    seller_feedback int,
    price_usd      numeric(10,2),
    price_max_usd  numeric(10,2),
    prev_price     numeric(10,2),
    currency       text default 'USD',
    image_url      text,
    condition      text,
    buying_format  text,
    shipping_text  text,
    free_shipping  boolean,
    location       text,
    quantity_text  text,
    quantity_avail int,
    units_badge    int,          -- floor: "63+ sold" -> 63
    prev_units     int,
    watchers       int,
    prev_watchers  int,
    first_seen     timestamptz not null default now(),
    last_seen      timestamptz not null default now(),
    obs_count      int not null default 1,
    starred        boolean not null default false,
    search_tsv     tsvector generated always as (
                       setweight(to_tsvector('english', coalesce(title, '')), 'A')
                   ) stored
);
create index if not exists eb_listings_search_idx on public.eb_listings using gin (search_tsv);
create index if not exists eb_listings_units_idx  on public.eb_listings (units_badge desc nulls last);
create index if not exists eb_listings_seen_idx   on public.eb_listings (last_seen desc);

-- ──────────────────────────────── observations ───────────────────────────────
create table if not exists public.eb_observations (
    id             bigserial primary key,
    eb_id          text not null references public.eb_listings(eb_id) on delete cascade,
    run_id         text not null,
    keyword        text not null,
    page           int,
    position       int,
    captured_at    timestamptz not null default now(),
    price_usd      numeric(10,2),
    units_badge    int,
    watchers       int,
    quantity_avail int,
    unique (eb_id, run_id)
);
create index if not exists eb_obs_run_idx  on public.eb_observations (run_id);
create index if not exists eb_obs_kw_idx   on public.eb_observations (keyword, captured_at desc);

create table if not exists public.eb_listing_keywords (
    eb_id      text not null references public.eb_listings(eb_id) on delete cascade,
    keyword    text not null,
    best_page  int,
    best_pos   int,
    last_seen  timestamptz not null default now(),
    primary key (eb_id, keyword)
);

-- ─────────────────────────────────── runs ────────────────────────────────────
create table if not exists public.eb_runs (
    id            bigserial primary key,
    run_id        text unique not null,
    keyword       text,
    page_from     int,
    page_to       int,
    depth         int,
    status        text,
    stopped_reason text,
    listings      int default 0,
    rows_written  int default 0,
    challenges    int default 0,
    tier          text,
    duration_s    int,
    started_at    timestamptz,
    finished_at   timestamptz
);
create index if not exists eb_runs_kw_idx on public.eb_runs (keyword, started_at desc);

-- ───────────────────────────────── settings ──────────────────────────────────
create table if not exists public.eb_settings (
    key   text primary key,
    value jsonb not null
);

-- ──────────────────────────────── claim / finish ─────────────────────────────
create or replace function public.eb_claim_slice(p_pages int default null)
returns table (keyword text, page_from int, page_to int, depth int)
language plpgsql security definer set search_path = public as $$
declare
    k public.eb_keywords;
    pages int;
begin
    select * into k
      from public.eb_keywords
     where status in ('queued','running')
       and due_at <= now()
     order by due_at asc, priority desc, id asc
     limit 1
     for update skip locked;

    if not found then return; end if;

    if k.next_page > k.depth then
        update public.eb_keywords
           set pass_no = k.pass_no + 1, next_page = 1, passes_done = passes_done + 1
         where id = k.id
        returning * into k;
    end if;

    pages := greatest(1, least(coalesce(p_pages, k.depth - k.next_page + 1), k.depth - k.next_page + 1));

    update public.eb_keywords
       set status = 'running',
           last_run_at = now(),
           due_at = now() + make_interval(hours => interval_hours)
     where id = k.id;

    return query select k.keyword, k.next_page, k.next_page + pages - 1, k.depth;
end;
$$;

create or replace function public.eb_finish_slice(
    p_keyword    text,
    p_next_page  int,
    p_status     text default null,
    p_listings   int  default 0,
    p_pages      int  default 1
) returns void
language plpgsql security definer set search_path = public as $$
begin
    update public.eb_keywords
       set next_page     = greatest(1, p_next_page),
           status        = coalesce(p_status,
                              case when p_status is null and p_next_page > depth
                                        then 'queued' else 'queued' end),
           pages_done    = pages_done + greatest(0, p_pages),
           listings_seen = listings_seen + greatest(0, p_listings)
     where keyword = p_keyword;
end;
$$;

-- ─────────────────────────────────── ingest ──────────────────────────────────
-- Idempotent per (run_id, eb_id): re-running an import writes nothing twice and
-- never clobbers prev_* with the same value.
create or replace function public.eb_ingest_batch(
    p_run_id  text,
    p_keyword text,
    p_rows    jsonb
) returns int
language plpgsql security definer set search_path = public as $$
declare
    r      jsonb;
    n      int := 0;
    v_units int;
    v_watch int;
begin
    for r in select * from jsonb_array_elements(p_rows) loop
        v_units := nullif(r->>'units_badge','')::int;
        v_watch := nullif(r->>'watchers','')::int;

        insert into public.eb_listings as l (
            eb_id, url, title, seller, seller_feedback, price_usd, price_max_usd,
            image_url, condition, buying_format, shipping_text, free_shipping,
            location, quantity_text, quantity_avail, units_badge, watchers
        ) values (
            r->>'eb_id', r->>'url', r->>'title', r->>'seller',
            nullif(r->>'seller_feedback','')::int,
            nullif(r->>'price_usd','')::numeric,
            nullif(r->>'price_max_usd','')::numeric,
            r->>'image_url', r->>'condition', r->>'buying_format', r->>'shipping_text',
            (r->>'free_shipping')::boolean,
            r->>'location', r->>'quantity_text',
            nullif(r->>'quantity_avail','')::int, v_units, v_watch
        )
        on conflict (eb_id) do update set
            title         = excluded.title,
            seller        = coalesce(excluded.seller, l.seller),
            price_usd     = excluded.price_usd,
            prev_price    = l.price_usd,
            price_max_usd = excluded.price_max_usd,
            image_url     = coalesce(excluded.image_url, l.image_url),
            condition     = excluded.condition,
            buying_format = excluded.buying_format,
            shipping_text = excluded.shipping_text,
            free_shipping = excluded.free_shipping,
            location      = excluded.location,
            quantity_text = excluded.quantity_text,
            quantity_avail= excluded.quantity_avail,
            prev_units    = l.units_badge,
            units_badge   = coalesce(excluded.units_badge, l.units_badge),
            prev_watchers = l.watchers,
            watchers      = coalesce(excluded.watchers, l.watchers),
            last_seen     = now(),
            obs_count     = l.obs_count + 1;

        insert into public.eb_observations (
            eb_id, run_id, keyword, page, position, price_usd, units_badge,
            watchers, quantity_avail
        ) values (
            r->>'eb_id', p_run_id, p_keyword,
            nullif(r->>'page','')::int, nullif(r->>'position','')::int,
            nullif(r->>'price_usd','')::numeric, v_units, v_watch,
            nullif(r->>'quantity_avail','')::int
        )
        on conflict (eb_id, run_id) do nothing;

        insert into public.eb_listing_keywords (eb_id, keyword, best_page, best_pos, last_seen)
        values (r->>'eb_id', p_keyword,
                nullif(r->>'page','')::int, nullif(r->>'position','')::int, now())
        on conflict (eb_id, keyword) do update set
            best_page = least(coalesce(eb_listing_keywords.best_page, 9999),
                              coalesce(excluded.best_page, 9999)),
            best_pos  = least(coalesce(eb_listing_keywords.best_pos, 9999),
                              coalesce(excluded.best_pos, 9999)),
            last_seen = now();

        n := n + 1;
    end loop;
    return n;
end;
$$;

-- ─────────────────────────────── keyword board ───────────────────────────────
-- One row per keyword: what the leaderboard renders. Attrition compares the two
-- most recent successful runs of that keyword, so a short lap cannot fake it.
create or replace view public.eb_keyword_board as
with latest as (
    select distinct on (keyword) keyword, run_id, started_at
      from public.eb_runs
     where status = 'succeeded'
     order by keyword, started_at desc
), previous as (
    select distinct on (keyword) keyword, run_id
      from public.eb_runs
     where status = 'succeeded'
       and run_id not in (select run_id from latest)
     order by keyword, started_at desc
), agg as (
    select k.keyword,
           k.depth, k.status, k.interval_hours, k.due_at, k.last_run_at,
           k.passes_done,
           count(l.eb_id)                                   as listings,
           count(l.eb_id) filter (where l.units_badge is not null) as badged,
           coalesce(sum(l.units_badge), 0)                  as total_units,
           coalesce(sum(greatest(coalesce(l.units_badge,0) - coalesce(l.prev_units, l.units_badge, 0), 0)), 0)
                                                            as units_gained,
           coalesce(sum(l.watchers), 0)                     as watchers,
           percentile_cont(0.5) within group (order by l.price_usd) as median_price,
           count(l.eb_id) filter (where l.prev_units is null) as first_sightings,
           max(l.price_usd)                                 as max_price
      from public.eb_keywords k
      left join public.eb_listing_keywords lk on lk.keyword = k.keyword
      left join public.eb_listings l on l.eb_id = lk.eb_id
     group by k.keyword, k.depth, k.status, k.interval_hours, k.due_at, k.last_run_at, k.passes_done
), att as (
    select p.keyword,
           count(*) filter (where c.eb_id is null) as vanished,
           count(*)                                as prev_count
      from previous p
      join public.eb_observations o on o.run_id = p.run_id
      left join public.eb_observations c
             on c.keyword = p.keyword
            and c.eb_id = o.eb_id
            and c.run_id = (select run_id from latest x where x.keyword = p.keyword)
     group by p.keyword
)
select a.*,
       case when att.prev_count > 0
            then round(att.vanished::numeric / att.prev_count, 3) end as attrition,
       att.vanished,
       (select started_at from latest x where x.keyword = a.keyword) as last_run_at_latest,
       case when a.badged >= 10 then 'ranked' else 'unmeasured' end as gate,
       case when a.units_gained > 0
            then round(a.units_gained::numeric /
                       greatest(extract(epoch from (
                           now() - (select started_at from latest x where x.keyword = a.keyword)
                       )) / 86400.0, 0.5), 2)
       end as units_per_day
  from agg a
  left join att on att.keyword = a.keyword;

-- ────────────────────────────────── pruning ──────────────────────────────────
create or replace function public.eb_prune(p_keep_days int default 7)
returns void language plpgsql security definer set search_path = public as $$
begin
    delete from public.eb_observations o
     where o.captured_at < now() - make_interval(days => p_keep_days)
       and exists (
           select 1 from public.eb_observations n
            where n.eb_id = o.eb_id
              and n.captured_at > o.captured_at
              and date_trunc('day', n.captured_at) = date_trunc('day', o.captured_at)
       );
end;
$$;

-- ─────────────────────────────── token-gated writes ──────────────────────────
create or replace function public.eb_check_token(p_token text)
returns boolean language plpgsql security definer set search_path = public, extensions as $$
declare ok boolean;
begin
    select (value->>'sha256') = encode(digest(p_token, 'sha256'), 'hex')
      into ok
      from public.eb_settings where key = 'write_token';
    return coalesce(ok, false);
end;
$$;

create or replace function public.eb_add_keyword(
    p_token text, p_keyword text, p_depth int default 10, p_interval_hours int default 48
) returns void language plpgsql security definer set search_path = public as $$
begin
    if not public.eb_check_token(p_token) then
        raise exception 'bad token' using errcode = '42501';
    end if;
    insert into public.eb_keywords (keyword, depth, interval_hours, added_by)
    values (lower(trim(p_keyword)), greatest(1, least(40, coalesce(p_depth,10))),
            greatest(6, least(720, coalesce(p_interval_hours,48))), 'web')
    on conflict (keyword) do nothing;
end;
$$;

create or replace function public.eb_set_status(p_token text, p_keyword text, p_status text)
returns void language plpgsql security definer set search_path = public as $$
begin
    if not public.eb_check_token(p_token) then
        raise exception 'bad token' using errcode = '42501';
    end if;
    if p_status not in ('queued','paused') then
        raise exception 'bad status';
    end if;
    update public.eb_keywords
       set status = p_status,
           due_at = case when p_status = 'queued' then now() else due_at end
     where keyword = p_keyword;
end;
$$;

create or replace function public.eb_delete_keyword(p_token text, p_keyword text)
returns void language plpgsql security definer set search_path = public as $$
begin
    if not public.eb_check_token(p_token) then
        raise exception 'bad token' using errcode = '42501';
    end if;
    delete from public.eb_keywords where keyword = p_keyword;
end;
$$;

create or replace function public.eb_set_star(p_token text, p_eb_id text, p_on boolean)
returns void language plpgsql security definer set search_path = public as $$
begin
    if not public.eb_check_token(p_token) then
        raise exception 'bad token' using errcode = '42501';
    end if;
    update public.eb_listings set starred = coalesce(p_on, false) where eb_id = p_eb_id;
end;
$$;

-- ───────────────────────────────────── RLS ───────────────────────────────────
alter table public.eb_keywords        enable row level security;
alter table public.eb_listings        enable row level security;
alter table public.eb_observations    enable row level security;
alter table public.eb_listing_keywords enable row level security;
alter table public.eb_runs            enable row level security;
alter table public.eb_settings        enable row level security;

drop policy if exists eb_read_keywords on public.eb_keywords;
create policy eb_read_keywords on public.eb_keywords for select to anon, authenticated using (true);
drop policy if exists eb_read_listings on public.eb_listings;
create policy eb_read_listings on public.eb_listings for select to anon, authenticated using (true);
drop policy if exists eb_read_obs on public.eb_observations;
create policy eb_read_obs on public.eb_observations for select to anon, authenticated using (true);
drop policy if exists eb_read_lk on public.eb_listing_keywords;
create policy eb_read_lk on public.eb_listing_keywords for select to anon, authenticated using (true);
drop policy if exists eb_read_runs on public.eb_runs;
create policy eb_read_runs on public.eb_runs for select to anon, authenticated using (true);
-- eb_settings is deliberately NOT readable: the token hash is not public.

grant usage on schema public to anon, authenticated;
grant select on public.eb_keywords, public.eb_listings, public.eb_observations,
                public.eb_listing_keywords, public.eb_runs, public.eb_keyword_board
  to anon, authenticated;
grant execute on function public.eb_add_keyword(text, text, int, int) to anon, authenticated;
grant execute on function public.eb_set_status(text, text, text)       to anon, authenticated;
grant execute on function public.eb_delete_keyword(text, text)         to anon, authenticated;
grant execute on function public.eb_set_star(text, text, boolean)      to anon, authenticated;
grant execute on function public.eb_check_token(text)                  to anon, authenticated;
