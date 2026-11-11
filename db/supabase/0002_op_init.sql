-- 0002 — Carson opportunity pipeline (op_*)
--
-- Kept separate from eb_* on purpose: the legacy crawler's tables encode
-- search-card semantics (units = a badge *floor*, watchers often absent).
-- This pipeline is item-first: one row per listing, exact watchers and exact
-- units from the item page, plus a snapshot per capture so velocity is
-- measurable. Mixing the two would corrupt both.

create table if not exists public.op_niches (
    keyword     text primary key,
    enabled     boolean not null default true,
    priority    integer not null default 0,
    depth       integer not null default 2,          -- graph hops from a seed
    per_run     integer not null default 60,         -- item pages per run
    tokens      text[]  not null default '{}',       -- title tokens that keep graph expansion on-niche
    source      text,                                -- where the niche came from (video id / manual)
    note        text,
    added_at    timestamptz not null default now()
);

create table if not exists public.op_items (
    item_id           text primary key,
    url               text not null,
    title             text,
    niche             text references public.op_niches(keyword) on delete set null,
    price_usd         numeric(12,2),
    currency          text,
    condition         text,
    watchers          integer,
    units_sold        integer,
    units_is_floor    boolean not null default false,
    quantity_avail    text,
    quantity_n        integer,
    seller            text,
    seller_feedback_pct numeric(5,2),
    seller_items_sold bigint,
    free_shipping     boolean,
    shipping_text     text,
    returns_text      text,
    location          text,
    image_url         text,
    state             text not null default 'new',   -- new | enriched | dead
    source            text not null default 'seed',  -- seed | related
    depth             integer not null default 0,
    attempts          integer not null default 0,
    last_error        text,
    first_seen        timestamptz not null default now(),
    last_seen         timestamptz
);

create index if not exists op_items_queue_idx on public.op_items (state, attempts, last_seen nulls first);
create index if not exists op_items_niche_idx on public.op_items (niche, watchers desc nulls last);
create index if not exists op_items_seen_idx  on public.op_items (last_seen desc nulls last);

create table if not exists public.op_snapshots (
    id           bigserial primary key,
    item_id      text not null references public.op_items(item_id) on delete cascade,
    run_id       text,
    captured_at  timestamptz not null default now(),
    watchers     integer,
    units_sold   integer,
    price_usd    numeric(12,2),
    quantity_n   integer
);
create index if not exists op_snapshots_item_idx on public.op_snapshots (item_id, captured_at desc);

create table if not exists public.op_edges (
    src_item text not null,
    dst_item text not null,
    niche    text,
    depth    integer not null default 1,
    seen_at  timestamptz not null default now(),
    primary key (src_item, dst_item)
);

create table if not exists public.op_runs (
    run_id      text primary key,
    niche       text,
    lane        text,
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    fetched     integer not null default 0,
    ok          integer not null default 0,
    blocked     integer not null default 0,
    failed      integer not null default 0,
    discovered  integer not null default 0,
    note        text
);

create table if not exists public.op_scores (
    item_id         text primary key references public.op_items(item_id) on delete cascade,
    scored_at       timestamptz not null default now(),
    verdict         text,          -- copy | watch | pass | unmeasured
    action          text,          -- copy now | find source first | watch | ignore
    score           numeric(6,2),
    demand          numeric(5,3),
    economics       numeric(5,3),
    competition     numeric(5,3),
    watchers_per_day numeric(8,3),
    units_per_day   numeric(8,3),
    profit_per_unit numeric(10,2),
    est_profit_month numeric(12,2),
    reasons         jsonb
);

-- Claim: the only concurrency-sensitive operation. Skip locking for now → the
-- harvester runs one worker per niche and `attempts` bounds retries.
create or replace function public.op_claim_items(
    p_niche text,
    p_limit integer default 60,
    p_stale_hours integer default 36
) returns setof public.op_items
language sql stable as $$
    select *
    from public.op_items
    where niche = p_niche
      and state <> 'dead'
      and attempts < 4
      and (last_seen is null or last_seen < now() - make_interval(hours => p_stale_hours))
    order by watchers desc nulls last, units_sold desc nulls last, attempts asc, first_seen asc
    limit p_limit
$$;

create or replace function public.op_queue_stats()
returns table (niche text, total bigint, fresh bigint, due bigint, dead bigint)
language sql stable as $$
    select niche,
           count(*) as total,
           count(*) filter (where last_seen > now() - interval '36 hours') as fresh,
           count(*) filter (where last_seen is null or last_seen < now() - interval '36 hours') as due,
           count(*) filter (where state = 'dead') as dead
    from public.op_items
    group by niche
    order by total desc
$$;

notify pgrst, 'reload schema';
