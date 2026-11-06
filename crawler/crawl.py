#!/usr/bin/env python3
"""Crawl one keyword slice of the eBay Active Surface and store it.

Two modes:
  queue mode (runner):  no --keyword -> claim whatever the DB says is due
  direct mode (debug):  --keyword "funny tshirt" --pages 2

Outputs out/rows.jsonl (one listing per line) and out/meta.json, and ingests to
Supabase when SUPABASE_URL + SUPABASE_SERVICE_KEY are set.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawler import supa  # noqa: E402
from crawler.fetch import Fetcher, pace  # noqa: E402
from crawler.parse import parse_result_count, parse_search_html  # noqa: E402

OUT = Path("out")


def crawl_keyword(keyword: str, pages: int, start_page: int = 1,
                  save_html: int = 0, allow_browser: bool = True) -> dict:
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{os.urandom(3).hex()}"
    fetcher = Fetcher(allow_browser=allow_browser, save_html=save_html)
    rows: list[dict] = []
    seen: set[str] = set()
    stopped = "depth"
    result_count = None
    last_good = start_page - 1
    t0 = time.time()

    try:
        for page in range(start_page, start_page + pages):
            page_rows: list[dict] = []
            tier = "http"
            # A page that parses to zero cards is a fetch failure, not the end of
            # results — eBay occasionally hands back a stub page the block detector
            # does not recognise. Retry once before believing it.
            for attempt in range(2):
                html, tier = fetcher.search_page(keyword, page)
                if result_count is None:
                    result_count = parse_result_count(html)
                page_rows = parse_search_html(html)
                if page_rows:
                    break
                print(f"[crawl] {keyword!r} p{page} [{tier}] 0 cards "
                      f"(attempt {attempt + 1}/2)", flush=True)
                if attempt == 0:
                    pace()

            if not page_rows:
                stopped = "empty_page"
                break

            fresh = [r for r in page_rows if r["eb_id"] not in seen]
            for r in fresh:
                seen.add(r["eb_id"])
                r["page"] = page
                r["keyword"] = keyword
            rows.extend(fresh)
            print(f"[crawl] {keyword!r} p{page} [{tier}] "
                  f"{len(page_rows)} cards, {len(fresh)} new", flush=True)
            if not fresh:
                stopped = "no_new_items"
                break
            last_good = page
            pace()
    except Exception as e:  # noqa: BLE001
        stopped = f"fail:{type(e).__name__}"
        print(f"[crawl] {keyword!r} stopped: {e}", flush=True)
    finally:
        fetcher.close()

    ended = start_page + pages - 1
    pages_done = len({r["page"] for r in rows}) if rows else 0

    # On an empty page, resume AT the failed page next lap instead of skipping the
    # hole — otherwise every later page of the keyword is lost for that pass.
    next_page_hint = (last_good + 1) if stopped == "empty_page" else ended + 1

    meta = {
        "run_id": run_id,
        "keyword": keyword,
        "page_from": start_page,
        "page_to": ended,
        "depth": pages,
        "status": "succeeded" if rows and not stopped.startswith("fail") else "failed",
        "stopped_reason": stopped,
        "listings": len(rows),
        "rows_written": 0,
        "challenges": fetcher.challenges,
        "tier": "browser" if "browser" in fetcher.tier_used else "http",
        "duration_s": round(time.time() - t0, 1),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "result_count": result_count,
        "pages_done": pages_done,
        "next_page_hint": next_page_hint,
    }
    return {"rows": rows, "meta": meta}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword")
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--save-html", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--max-keywords", type=int, default=40)
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    use_db = supa.enabled() and not args.no_db

    slices: list[dict] = []
    if args.keyword:
        slices.append({"keyword": args.keyword.lower(), "page_from": args.start_page,
                       "page_to": args.start_page + args.pages - 1, "depth": args.pages})
    elif use_db:
        for _ in range(args.max_keywords):
            got = supa.claim_slice(pages=args.pages)
            if not got:
                break
            slices.append(got)
            print(f"[queue] claimed {got['keyword']!r} pages "
                  f"{got['page_from']}-{got['page_to']} of {got['depth']}", flush=True)
    else:
        print("nothing to do: no --keyword and DB not configured", file=sys.stderr)
        return 2

    all_rows: list[dict] = []
    all_meta: list[dict] = []
    failures = 0

    for s in slices:
        keyword = s["keyword"]
        pages = int(s["page_to"]) - int(s["page_from"]) + 1
        out = crawl_keyword(keyword, pages, start_page=int(s["page_from"]),
                            save_html=args.save_html,
                            allow_browser=not args.no_browser)
        rows, meta = out["rows"], out["meta"]
        meta["slot_depth"] = s["depth"]

        if use_db:
            # Each DB step is isolated: a telemetry failure must never stop the
            # queue from advancing, or a keyword gets stuck in 'running' forever.
            try:
                if rows:
                    meta["rows_written"] = supa.ingest(meta["run_id"], keyword, rows)
            except Exception as e:  # noqa: BLE001
                print(f"[db] ingest {keyword!r}: {e}", flush=True)
                failures += 1
            try:
                supa.upsert_run(meta)
            except Exception as e:  # noqa: BLE001
                print(f"[db] run-log {keyword!r} (non-fatal): {e}", flush=True)
            try:
                next_page = meta.get("next_page_hint") or (int(s["page_to"]) + 1)
                supa.finish_slice(keyword, next_page, "queued",
                                  listings=len(rows), pages=meta["pages_done"])
            except Exception as e:  # noqa: BLE001
                print(f"[db] finish-slice {keyword!r}: {e}", flush=True)
                failures += 1

        all_rows.extend(rows)
        all_meta.append(meta)
        print(f"[done] {keyword!r}: {len(rows)} rows, {meta['duration_s']}s, "
              f"{meta['stopped_reason']}, tier={meta['tier']}", flush=True)

    (OUT / "rows.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows))
    (OUT / "meta.json").write_text(json.dumps(all_meta, indent=2))

    if use_db and args.max_keywords:
        try:
            supa.prune(7)
        except Exception as e:  # noqa: BLE001
            print(f"[db] prune: {e}", flush=True)

    print(json.dumps({"slices": len(all_meta), "rows": len(all_rows),
                      "failures": failures}, indent=2))
    return 0 if all_rows or not slices else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
