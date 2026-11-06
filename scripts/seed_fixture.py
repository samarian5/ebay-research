#!/usr/bin/env python3
"""Seed a keyword + listings from a saved real capture (offline ingest check).

  python scripts/seed_fixture.py --file tests/fixtures/real/search_active_2026-11-06.html.gz \
                                 --keyword "funny tshirt"

Uses the same eb_ingest_batch RPC the crawler uses, so it exercises the real write
path — not a mock. Data is genuine eBay markup captured 2026-11-06.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from crawler.parse import parse_result_count, parse_search_html  # noqa: E402


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (ROOT / ".env", ROOT.parent / "etsy-scraper" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out.setdefault(k.strip(), v.strip().strip('"'))
    out.update({k: v for k, v in os.environ.items() if k.startswith("SUPABASE")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--keyword", default="funny tshirt")
    ap.add_argument("--pages", type=int, default=1)
    a = ap.parse_args()

    e = env()
    url = e["SUPABASE_URL"].rstrip("/")
    key = e["SUPABASE_SERVICE_KEY"]
    hdr = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    raw = Path(a.file).read_bytes()
    html = gzip.decompress(raw).decode("utf-8", "replace") if a.file.endswith(".gz") else raw.decode()
    rows = parse_search_html(html)
    for i, r in enumerate(rows):
        r["page"] = 1
        r["position"] = i + 1
    print(f"parsed {len(rows)} listings; result count: {parse_result_count(html)}")
    print(f"badged: {sum(1 for r in rows if r['units_badge'])}  "
          f"watchers: {sum(1 for r in rows if r['watchers'])}")

    # keyword row (idempotent)
    r = requests.post(f"{url}/rest/v1/eb_keywords",
                      headers={**hdr, "Prefer": "resolution=merge-duplicates,return=minimal"},
                      json=[{"keyword": a.keyword.lower(), "depth": 10, "interval_hours": 48,
                             "status": "queued", "added_by": "seed"}], timeout=30)
    print("keyword upsert:", r.status_code)

    run_id = f"seed-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    total = 0
    for i in range(0, len(rows), 100):
        rr = requests.post(f"{url}/rest/v1/rpc/eb_ingest_batch", headers=hdr,
                           json={"p_run_id": run_id, "p_keyword": a.keyword.lower(),
                                 "p_rows": rows[i:i + 100]}, timeout=60)
        if rr.status_code >= 300:
            print("ingest failed:", rr.status_code, rr.text[:400])
            return 1
        total += rr.json() or 0
    print("rows ingested:", total)

    meta = {"run_id": run_id, "keyword": a.keyword.lower(), "page_from": 1, "page_to": 1,
            "depth": 10, "status": "succeeded", "stopped_reason": "fixture",
            "listings": len(rows), "rows_written": total, "challenges": 0, "tier": "fixture",
            "duration_s": 0, "started_at": datetime.now(timezone.utc).isoformat()}
    rr = requests.post(f"{url}/rest/v1/eb_runs",
                       headers={**hdr, "Prefer": "resolution=merge-duplicates,return=minimal"},
                       json=[meta], timeout=30)
    print("run row:", rr.status_code, rr.text[:200])

    time.sleep(1)
    board = requests.get(f"{url}/rest/v1/eb_keyword_board?select=*",
                         headers={"apikey": e["SUPABASE_ANON_KEY"],
                                  "Authorization": f"Bearer {e['SUPABASE_ANON_KEY']}"}, timeout=30)
    print("anon board read:", board.status_code)
    print(json.dumps(board.json(), indent=2)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
