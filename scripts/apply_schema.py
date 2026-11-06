#!/usr/bin/env python3
"""Apply the eb_ schema to Supabase and seed the write token.

  python scripts/apply_schema.py            # apply db/supabase/*.sql in order
  python scripts/apply_schema.py --smoke    # only run the smoke checks

Reads credentials from .env (this project) or ../etsy-scraper/.env as a fallback,
because both point at the same Supabase project.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_CANDIDATES = [ROOT / ".env", ROOT.parent / "etsy-scraper" / ".env"]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def connect(env: dict[str, str]):
    import psycopg

    ref = env["SUPABASE_PROJECT_REF"]
    pwd = env["SUPABASE_DB_PASS"]
    last = None
    for host, user in ((f"db.{ref}.supabase.co", "postgres"),
                       (f"aws-0-eu-central-1.pooler.supabase.com", f"postgres.{ref}"),
                       (f"aws-0-us-east-1.pooler.supabase.com", f"postgres.{ref}"),
                       (f"aws-0-us-west-1.pooler.supabase.com", f"postgres.{ref}")):
        try:
            return psycopg.connect(host=host, port=5432, dbname="postgres",
                                   user=user, password=pwd,
                                   connect_timeout=10, autocommit=True)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  connect failed via {host}: {type(e).__name__}")
    raise SystemExit(f"could not connect: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    env = load_env()
    missing = [k for k in ("SUPABASE_PROJECT_REF", "SUPABASE_DB_PASS") if not env.get(k)]
    if missing:
        raise SystemExit(f"missing env: {missing}")

    with connect(env) as conn, conn.cursor() as cur:
        if not args.smoke:
            for sql in sorted((ROOT / "db" / "supabase").glob("*.sql")):
                print(f"applying {sql.name} …", flush=True)
                cur.execute(sql.read_text())

            # Seed the write token once; never overwrite an existing one.
            token = env.get("EB_WRITE_TOKEN") or secrets.token_urlsafe(24)
            cur.execute(
                """insert into public.eb_settings (key, value)
                   values ('write_token',
                           jsonb_build_object('sha256', encode(digest(%s,'sha256'),'hex')))
                   on conflict (key) do nothing""",
                (token,),
            )
            if not env.get("EB_WRITE_TOKEN"):
                with open(ROOT / ".env", "a") as fh:
                    fh.write(f"\nEB_WRITE_TOKEN={token}\n")
                print("write token generated -> appended to .env (EB_WRITE_TOKEN)")

        print("\nsmoke checks")
        cur.execute("select count(*) from public.eb_keywords")
        print("  eb_keywords rows:", cur.fetchone()[0])
        cur.execute("select count(*) from public.eb_listings")
        print("  eb_listings rows:", cur.fetchone()[0])
        cur.execute("select count(*) from public.eb_keyword_board")
        print("  board rows:", cur.fetchone()[0])
        cur.execute("select proname from pg_proc where proname like 'eb\\_%' order by 1")
        print("  functions:", [r[0] for r in cur.fetchall()])
        cur.execute("select status, count(*) from public.eb_keywords group by 1")
        print("  keyword status:", cur.fetchall())
    return 0


if __name__ == "__main__":
    sys.exit(main())
