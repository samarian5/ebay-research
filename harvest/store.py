"""Supabase write layer for the op_* tables (PostgREST, service key).

Only the fields we actually observed are ever sent: `merge-duplicates` with a
null column would erase a good value from an earlier run, so None is stripped
before the call.
"""
from __future__ import annotations

import json
import os

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _headers(extra: dict | None = None) -> dict:
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def delete(path: str) -> int:
    r = requests.delete(f"{URL}/rest/v1/{path}", headers=_headers({"Prefer": "return=minimal"}), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"delete {path} -> {r.status_code}: {r.text[:200]}")
    return r.status_code


def rpc(fn: str, payload: dict, timeout: int = 90):
    r = requests.post(f"{URL}/rest/v1/rpc/{fn}", headers=_headers(), data=json.dumps(payload), timeout=timeout)
    if r.status_code >= 300:
        raise RuntimeError(f"rpc {fn} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else None


def upsert(table: str, rows: list[dict], conflict: str, chunk: int = 100, keep_nulls: bool = False,
           ignore_duplicates: bool = False) -> int:
    if not rows:
        return 0
    n = 0
    # PostgREST requires every object in one request to share the same keys
    # (PGRST102), so group rows by their key signature before sending.
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if not keep_nulls:
            r = {k: v for k, v in r.items() if v is not None}
        if r:
            groups.setdefault(tuple(sorted(r)), []).append(r)
    for part_all in groups.values():
        for i in range(0, len(part_all), chunk):
            part = part_all[i:i + chunk]
            res = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
            r = requests.post(f"{URL}/rest/v1/{table}?on_conflict={conflict}",
                              headers=_headers({"Prefer": f"resolution={res},return=minimal"}),
                              data=json.dumps(part), timeout=120)
            if r.status_code >= 300:
                raise RuntimeError(f"upsert {table} -> {r.status_code}: {r.text[:300]}")
            n += len(part)
    return n


def update(table: str, match: str, values: dict) -> int:
    """PATCH an existing row. Used for failure bookkeeping: an upsert here would
    try to INSERT when the row is missing and trip the NOT NULL url constraint."""
    r = requests.patch(f"{URL}/rest/v1/{table}?{match}",
                       headers=_headers({"Prefer": "return=minimal"}),
                       data=json.dumps(values), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"update {table} -> {r.status_code}: {r.text[:300]}")
    return 1


def insert(table: str, rows: list[dict], chunk: int = 200) -> int:
    if not rows:
        return 0
    n = 0
    for i in range(0, len(rows), chunk):
        r = requests.post(f"{URL}/rest/v1/{table}",
                          headers=_headers({"Prefer": "return=minimal"}),
                          data=json.dumps(rows[i:i + chunk]), timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"insert {table} -> {r.status_code}: {r.text[:300]}")
        n += len(rows[i:i + chunk])
    return n


STABLE_ORDER = {"op_items": "item_id", "op_scores": "item_id",
                "op_snapshots": "item_id,captured_at", "op_edges": "src_id"}


def select(path: str, page: int = 1000) -> list[dict]:
    """Paginated read.

    PostgREST caps a response at 1,000 rows regardless of ?limit=, so a plain GET
    silently loses data once a table passes that (op_items did). Two details are
    required for correctness: the caller's ?limit= must not cap the window, and an
    unordered range query can repeat or skip rows across pages, so a stable order
    is added when the caller did not supply one.
    """
    table = path.split("?")[0].strip("/")
    qs = path.split("?", 1)[1] if "?" in path else ""
    parts = [q for q in qs.split("&") if q and not q.startswith("limit=")]
    if not any(q.startswith("order=") for q in parts) and table in STABLE_ORDER:
        parts.append(f"order={STABLE_ORDER[table]}")
    base = f"{URL}/rest/v1/{table}" + ("?" + "&".join(parts) if parts else "")

    out: list[dict] = []
    start = 0
    while True:
        r = requests.get(base, headers=_headers({
            "Range": f"{start}-{start + page - 1}",
            "Range-Unit": "items",
        }), timeout=90)
        if r.status_code == 416:        # asked past the end: a normal stop, not an error
            return out
        if r.status_code >= 300:
            raise RuntimeError(f"select {path} -> {r.status_code}: {r.text[:200]}")
        part = r.json()
        out.extend(part)
        if len(part) < page:
            return out
        start += page
