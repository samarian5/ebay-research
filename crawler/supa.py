"""Supabase REST client for the eb_ schema (service role, runner-side only)."""
from __future__ import annotations

import json
import os

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""


def enabled() -> bool:
    return bool(URL and KEY)


def _headers() -> dict:
    return {"apikey": KEY, "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json", "Prefer": "return=representation"}


def rpc(name: str, payload: dict, timeout: int = 60):
    r = requests.post(f"{URL}/rest/v1/rpc/{name}", headers=_headers(),
                      data=json.dumps(payload), timeout=timeout)
    if r.status_code >= 300:
        raise RuntimeError(f"rpc {name} -> {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        return None


def claim_slice(pages: int | None = None):
    rows = rpc("eb_claim_slice", {"p_pages": pages})
    return rows[0] if rows else None


def finish_slice(keyword: str, next_page: int, status: str | None = None,
                 listings: int = 0, pages: int = 1) -> None:
    rpc("eb_finish_slice", {"p_keyword": keyword, "p_next_page": next_page,
                            "p_status": status, "p_listings": listings,
                            "p_pages": pages})


def ingest(run_id: str, keyword: str, rows: list[dict], batch: int = 150) -> int:
    total = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        total += rpc("eb_ingest_batch", {"p_run_id": run_id, "p_keyword": keyword,
                                         "p_rows": chunk}) or 0
    return total


def upsert_run(meta: dict) -> None:
    r = requests.post(f"{URL}/rest/v1/eb_runs", headers={**_headers(),
                      "Prefer": "resolution=merge-duplicates,return=minimal"},
                      data=json.dumps([meta]), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"eb_runs upsert -> {r.status_code}: {r.text[:300]}")


def prune(keep_days: int = 7) -> None:
    rpc("eb_prune", {"p_keep_days": keep_days})
