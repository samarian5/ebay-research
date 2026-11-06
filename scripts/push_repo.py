#!/usr/bin/env python3
"""Publish this project to GitHub + turn on Pages, with no git binary needed.

Needs a classic PAT with `repo` + `workflow` scopes, read from either
  ~/.github-token      (preferred: one line, mode 600)
  $GITHUB_TOKEN

  python scripts/push_repo.py --dry-run        # show what would be uploaded
  python scripts/push_repo.py --repo ebay-research

Sets the two Actions secrets from .env (service key encrypted with the repo's
public key, so it never lands in the repo). Never prints secret values.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
SKIP_DIRS = {".git", "out", "__pycache__", ".venv", "node_modules", "cache",
             "downloaded_files", ".pytest_cache"}
SKIP_EXT = {".pyc", ".log", ".lock"}
SKIP_FILES = {".env"}


def token() -> str:
    p = Path.home() / ".github-token"
    t = p.read_text().strip() if p.exists() else os.environ.get("GITHUB_TOKEN", "")
    if not t:
        sys.exit("no token: put a PAT in ~/.github-token (chmod 600) or set GITHUB_TOKEN")
    return t


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (ROOT / ".env", ROOT.parent / "etsy-scraper" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return out


def files() -> list[Path]:
    out = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix in SKIP_EXT or p.name in SKIP_FILES:
            continue
        out.append(p)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="ebay-research")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    todo = files()
    print(f"{len(todo)} files to publish")
    if a.dry_run:
        for p in todo:
            print("  ", p.relative_to(ROOT))
        return 0

    tok = token()
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}

    me = requests.get(f"{API}/user", headers=h, timeout=20)
    if me.status_code != 200:
        sys.exit(f"token rejected ({me.status_code})")
    owner = me.json()["login"]
    print("owner:", owner)

    r = requests.post(f"{API}/user/repos", headers=h, timeout=30, json={
        "name": a.repo, "private": a.private, "auto_init": False,
        "description": "eBay opportunity board — keyword demand research, free stack"})
    if r.status_code == 201:
        print("repo created:", r.json()["html_url"])
    elif r.status_code == 422:
        print("repo exists, reusing")
    else:
        sys.exit(f"create repo -> {r.status_code}: {r.text[:300]}")

    branch = "main"
    for p in todo:
        rel = p.relative_to(ROOT).as_posix()
        body = {"message": f"add {rel}", "branch": branch,
                "content": base64.b64encode(p.read_bytes()).decode()}
        sha = requests.get(f"{API}/repos/{owner}/{a.repo}/contents/{rel}",
                           headers=h, timeout=20)
        if sha.status_code == 200:
            body["sha"] = sha.json()["sha"]
            body["message"] = f"update {rel}"
        put = requests.put(f"{API}/repos/{owner}/{a.repo}/contents/{rel}",
                           headers=h, data=json.dumps(body), timeout=40)
        print(f"  {'upd' if 'sha' in body else 'new'} {rel} -> {put.status_code}")
        if put.status_code >= 300:
            print("    ", put.text[:200])

    # Actions secrets (encrypted with the repo public key)
    env = load_env()
    pk = requests.get(f"{API}/repos/{owner}/{a.repo}/actions/secrets/public-key",
                      headers=h, timeout=20).json()
    from nacl import encoding, public
    box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
    for name, value in (("SUPABASE_URL", env.get("SUPABASE_URL")),
                        ("SUPABASE_SERVICE_KEY", env.get("SUPABASE_SERVICE_KEY"))):
        if not value:
            print(f"  !! {name} missing from .env, skipped")
            continue
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
        sr = requests.put(f"{API}/repos/{owner}/{a.repo}/actions/secrets/{name}",
                          headers=h, timeout=30,
                          json={"encrypted_value": enc, "key_id": pk["key_id"]})
        print(f"  secret {name}: {sr.status_code}")

    # Pages from /docs on main
    pg = requests.post(f"{API}/repos/{owner}/{a.repo}/pages", headers=h, timeout=30,
                       json={"source": {"branch": branch, "path": "/docs"}})
    if pg.status_code in (201, 204):
        print("pages enabled")
    elif pg.status_code == 409:
        print("pages already enabled")
    else:
        print("pages:", pg.status_code, pg.text[:200])

    print(f"\nsite:  https://{owner}.github.io/{a.repo}/")
    print(f"repo:  https://github.com/{owner}/{a.repo}")
    print("next:  Actions -> ebay-crawl -> Run workflow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
