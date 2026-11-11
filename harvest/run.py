"""Opportunity harvester: discovery → enrichment → queue growth.

    python -m harvest.run add-niche "pokemon card" --depth 2 --per-run 60
    python -m harvest.run discover --niche "pokemon card" --pages 2
    python -m harvest.run enrich   --niche "pokemon card" --limit 40
    python -m harvest.run harvest  --niche "pokemon card" --pages 2 --limit 40
    python -m harvest.run stats

Design notes
------------
* Discovery uses the search surface (60 cards/page); enrichment uses item pages
  because they state exact watchers and exact units. Both go through the
  translate proxy (see fetch.py for why).
* Graph growth: every enriched item contributes its related-item ids back into
  `op_items` as stubs, filtered by the niche's tokens. That is how a niche keeps
  finding new inventory once search is unavailable.
* Every fetch is recorded in `op_runs`; a blocked lane is reported, never
  worked around.
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote_plus

from .fetch import Fetcher
from .parse import parse_item, parse_search
from . import store

SEARCH = "https://www.ebay.com/sch/i.html?_nkw={q}&_ipg=60&_pgn={p}"
ITEM = "https://www.ebay.com/itm/{id}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(msg, flush=True)


def niche_row(keyword: str) -> dict | None:
    rows = store.select(f"op_niches?keyword=eq.{quote_plus(keyword)}&select=*")
    return rows[0] if rows else None


def matches(tokens: list[str], text: str) -> bool:
    if not tokens:
        return True
    low = (text or "").lower()
    return any(t in low for t in tokens)


# ---------------------------------------------------------------- discovery
def discover(keyword: str, pages: int, fetcher: Fetcher, run_id: str) -> dict:
    seen: dict[str, dict] = {}
    blocked = 0
    for page in range(1, pages + 1):
        f = fetcher.get(SEARCH.format(q=quote_plus(keyword), p=page))
        if f.blocked:
            blocked += 1
            log(f"  page {page}: blocked ({f.blocked})")
            break
        rows = parse_search(f.html, keyword)
        for r in rows:
            seen.setdefault(r["item_id"], r)
        log(f"  page {page}: {len(rows)} cards ({f.lane}, {f.secs}s)")

    rows = list(seen.values())
    written = store.upsert("op_items", rows, "item_id")
    log(f"  discovered {len(rows)} unique listings -> {written} upserted")
    return {"fetched": pages, "ok": pages - blocked, "blocked": blocked, "discovered": len(rows)}


# --------------------------------------------------------------- enrichment
def enrich(keyword: str, limit: int, fetcher: Fetcher, run_id: str) -> dict:
    niche = niche_row(keyword) or {}
    tokens = niche.get("tokens") or []
    max_depth = int(niche.get("depth") or 2)

    claimed = store.rpc("op_claim_items", {"p_niche": keyword, "p_limit": limit})
    log(f"  claimed {len(claimed)} items")
    ok = blocked = failed = new_stubs = 0

    for n, item in enumerate(claimed, 1):
        item_id = item["item_id"]
        f = fetcher.get(ITEM.format(id=item_id))
        if f.blocked:
            blocked += 1
            store.update("op_items", f"item_id=eq.{item_id}",
                         {"attempts": int(item.get("attempts") or 0) + 1, "last_error": f.blocked})
            log(f"  [{n}/{len(claimed)}] {item_id} blocked ({f.blocked})")
            continue
        try:
            data = parse_item(f.html, item_id)
        except Exception as e:  # noqa: BLE001
            failed += 1
            store.update("op_items", f"item_id=eq.{item_id}",
                         {"attempts": int(item.get("attempts") or 0) + 1,
                          "last_error": f"parse:{type(e).__name__}"})
            continue

        related = data.pop("related_ids", [])
        row = {"item_id": item_id, "url": ITEM.format(id=item_id), "niche": keyword,
               "state": "enriched", "last_seen": now(),
               "attempts": int(item.get("attempts") or 0), **data}
        if not row.get("title"):
            row["title"] = item.get("title")
        store.upsert("op_items", [row], "item_id")

        store.insert("op_snapshots", [{
            "item_id": item_id, "run_id": run_id, "captured_at": now(),
            "watchers": data.get("watchers"), "units_sold": data.get("units_sold"),
            "price_usd": data.get("price_usd"), "quantity_n": data.get("quantity_n"),
        }])

        # Grow the graph, but only on-niche and only within depth.
        depth = int(item.get("depth") or 0)
        if depth < max_depth:
            stubs = []
            for rid in related:
                if rid == item_id or not matches(tokens, item.get("title") or ""):
                    continue
                stubs.append({"item_id": rid, "url": ITEM.format(id=rid), "niche": keyword,
                              "source": "related", "depth": depth + 1, "state": "new"})
            if stubs:
                new_stubs += store.upsert("op_items", stubs, "item_id")
                store.upsert("op_edges", [{"src_item": item_id, "dst_item": s["item_id"],
                                           "niche": keyword, "depth": depth + 1} for s in stubs],
                             "src_item,dst_item")
        ok += 1
        if n % 10 == 0:
            log(f"  [{n}/{len(claimed)}] ok={ok} blocked={blocked} stubs+={new_stubs}")

    return {"fetched": len(claimed), "ok": ok, "blocked": blocked, "failed": failed,
            "discovered": new_stubs}


# --------------------------------------------------------------------- stats
def all_niches(pages: int, limit: int | None, fetcher: Fetcher) -> dict:
    """One pass over every enabled niche, honouring each niche's per_run budget."""
    niches = store.select("op_niches?enabled=is.true&select=keyword,per_run,depth&order=priority.desc,keyword")
    total = {"fetched": 0, "ok": 0, "blocked": 0, "failed": 0, "discovered": 0}
    for n in niches:
        kw = n["keyword"]
        cap = limit or int(n.get("per_run") or 40)
        run_id = f"h-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}"
        store.upsert("op_runs", [{"run_id": run_id, "niche": kw, "lane": "batch", "started_at": now()}],
                     "run_id")
        log(f"\n== {kw} (cap {cap})")
        t0 = time.time()
        d = discover(kw, pages, fetcher, run_id)
        e = enrich(kw, cap, fetcher, run_id)
        res = {k: d.get(k, 0) + e.get(k, 0) for k in total}
        store.upsert("op_runs", [{"run_id": run_id, "finished_at": now(),
                                  "fetched": res["fetched"], "ok": res["ok"],
                                  "blocked": res["blocked"], "failed": res["failed"],
                                  "discovered": res["discovered"],
                                  "note": f"{time.time() - t0:.0f}s"}], "run_id")
        for k in total:
            total[k] += res.get(k, 0)
    return total


def stats() -> None:
    rows = store.rpc("op_queue_stats", {})
    print(f"{'niche':<24}{'total':>7}{'fresh':>7}{'due':>7}{'dead':>6}")
    for r in rows or []:
        print(f"{r['niche'] or '-':<24}{r['total']:>7}{r['fresh']:>7}{r['due']:>7}{r['dead']:>6}")


def add_niche(keyword: str, depth: int, per_run: int, tokens: list[str]) -> None:
    store.upsert("op_niches", [{
        "keyword": keyword, "enabled": True, "depth": depth, "per_run": per_run,
        "tokens": tokens or [w for w in keyword.lower().split() if len(w) > 2],
    }], "keyword")
    log(f"niche '{keyword}' registered (depth {depth}, {per_run}/run, tokens {tokens or keyword.split()})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harvest.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-niche")
    a.add_argument("keyword")
    a.add_argument("--depth", type=int, default=2)
    a.add_argument("--per-run", type=int, default=60)
    a.add_argument("--tokens", default="")

    for name in ("discover", "enrich", "harvest"):
        p = sub.add_parser(name)
        p.add_argument("--niche", required=True)
        p.add_argument("--pages", type=int, default=2)
        p.add_argument("--limit", type=int, default=40)
        p.add_argument("--no-direct", action="store_true", help="skip the direct lane, proxy only")

    p = sub.add_parser("all")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--limit", type=int, default=0, help="override per-niche cap (0 = use niche setting)")
    p.add_argument("--no-direct", action="store_true")

    sub.add_parser("stats")
    args = ap.parse_args(argv)

    if args.cmd == "stats":
        stats(); return 0
    if args.cmd == "all":
        fetcher = Fetcher(direct_first=not args.no_direct)
        log(f"batch over all enabled niches · pages {args.pages}")
        res = all_niches(args.pages, args.limit or None, fetcher)
        log(f"batch done · {res}")
        return 0

    if args.cmd == "add-niche":
        add_niche(args.keyword, args.depth, args.per_run,
                  [t.strip().lower() for t in args.tokens.split(",") if t.strip()])
        return 0

    run_id = f"h-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}"
    fetcher = Fetcher(direct_first=not args.no_direct)
    t0 = time.time()

    store.upsert("op_runs", [{"run_id": run_id, "niche": args.niche, "lane": "harvest",
                              "started_at": now()}], "run_id")
    log(f"run {run_id} · niche '{args.niche}'")

    res: dict = {}
    try:
        if args.cmd in ("discover", "harvest"):
            log("discover:")
            res = discover(args.niche, args.pages, fetcher, run_id)
        if args.cmd in ("enrich", "harvest"):
            log("enrich:")
            e = enrich(args.niche, args.limit, fetcher, run_id)
            res = {k: res.get(k, 0) + v for k, v in e.items()} if res else e
    finally:
        store.upsert("op_runs", [{"run_id": run_id, "finished_at": now(),
                                  "fetched": res.get("fetched", 0), "ok": res.get("ok", 0),
                                  "blocked": res.get("blocked", 0), "failed": res.get("failed", 0),
                                  "discovered": res.get("discovered", 0),
                                  "note": f"{time.time() - t0:.0f}s"}], "run_id")

    log(f"done in {time.time() - t0:.0f}s · {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
