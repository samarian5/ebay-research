#!/usr/bin/env python3
"""Publish this project to GitHub through Composio's authenticated proxy.

No PAT, no git binary, no device flow: every call goes through
`composio proxy ... --toolkit github`, which uses the connected GitHub account.

  python scripts/composio_publish.py --dry-run
  python scripts/composio_publish.py --repo ebay-research
  python scripts/composio_publish.py --repo ebay-research --skip-pages

What it does
  1. creates the repo (public by default — unmetered Actions minutes)
  2. uploads every file as git blobs and lands them in ONE commit on main
  3. encrypts SUPABASE_URL / SUPABASE_SERVICE_KEY into Actions secrets
  4. enables Pages from /docs
  5. dispatches the crawl workflow and prints the run URL

Secret values are read from .env and never printed.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "out", "__pycache__", ".venv", "node_modules", "cache",
             "downloaded_files", ".pytest_cache"}
SKIP_EXT = {".pyc", ".log", ".lock"}
SKIP_FILES = {".env"}


# ───────────────────────────── proxy transport ─────────────────────────────
def proxy(method: str, path: str, body: dict | list | None = None) -> tuple[int, dict | list | str]:
    cmd = ["composio", "proxy", path, "--toolkit", "github", "-X", method]
    payload = None
    if body is not None:
        # Large blobs exceed MAX_ARG_STRLEN if passed as argv, so feed JSON on stdin.
        cmd += ["-d", "-"]
        payload = json.dumps(body)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                       input=payload)
    raw = (p.stdout or "").strip()
    if not raw:
        return (p.returncode, (p.stderr or "").strip()[:400])
    # The CLI wraps the response; dig out the JSON payload defensively.
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError:
        return (p.returncode, raw[:400])
    data = outer.get("data", outer) if isinstance(outer, dict) else outer
    if isinstance(data, dict) and "response_data" in data:
        data = data["response_data"]
    status = 200
    if isinstance(outer, dict):
        status = outer.get("status", outer.get("status_code", 200)) or 200
    return (int(status) if str(status).isdigit() else 200, data)


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="ebay-research")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-pages", action="store_true")
    ap.add_argument("--no-dispatch", action="store_true")
    a = ap.parse_args()

    todo = files()
    print(f"{len(todo)} files")

    if a.dry_run:
        for p in todo:
            print("  ", p.relative_to(ROOT).as_posix())
        return 0

    st, me = proxy("GET", "/user")
    if not isinstance(me, dict) or "login" not in me:
        print("GET /user failed:", st, str(me)[:300])
        return 1
    owner = me["login"]
    print("owner:", owner)

    st, repo = proxy("POST", "/user/repos", {
        "name": a.repo, "private": a.private, "auto_init": True,
        "description": "eBay opportunity board — keyword demand research on a free stack"})
    if st in (200, 201):
        print("repo created:", repo.get("html_url") if isinstance(repo, dict) else st)
    else:
        print("create repo ->", st, str(repo)[:200], "(continuing; may already exist)")

    # head of main, needed as the commit parent
    st, ref = proxy("GET", f"/repos/{owner}/{a.repo}/git/ref/heads/main")
    parent = ref.get("object", {}).get("sha") if isinstance(ref, dict) else None
    print("main head:", (parent or "none")[:10])

    tree = []
    for p in todo:
        rel = p.relative_to(ROOT).as_posix()
        content = base64.b64encode(p.read_bytes()).decode()
        st, blob = proxy("POST", f"/repos/{owner}/{a.repo}/git/blobs",
                         {"content": content, "encoding": "base64"})
        sha = blob.get("sha") if isinstance(blob, dict) else None
        if not sha:
            print(f"  !! blob failed for {rel}: {st} {str(blob)[:160]}")
            continue
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
        print(f"  blob {rel} -> {sha[:8]}")

    st, new_tree = proxy("POST", f"/repos/{owner}/{a.repo}/git/trees", {"tree": tree})
    tree_sha = new_tree.get("sha") if isinstance(new_tree, dict) else None
    if not tree_sha:
        print("tree failed:", st, str(new_tree)[:300])
        return 1
    print("tree:", tree_sha[:10], f"({len(tree)} files)")

    commit_body = {"message": "eBay opportunity board: crawler, schema, web UI",
                   "tree": tree_sha}
    if parent:
        commit_body["parents"] = [parent]
    st, commit = proxy("POST", f"/repos/{owner}/{a.repo}/git/commits", commit_body)
    commit_sha = commit.get("sha") if isinstance(commit, dict) else None
    if not commit_sha:
        print("commit failed:", st, str(commit)[:300])
        return 1

    st, _ = proxy("PATCH", f"/repos/{owner}/{a.repo}/git/refs/heads/main",
                  {"sha": commit_sha, "force": True})
    print("commit:", commit_sha[:10], "ref update:", st)

    # ── secrets ────────────────────────────────────────────────────────────
    env = load_env()
    st, pk = proxy("GET", f"/repos/{owner}/{a.repo}/actions/secrets/public-key")
    if isinstance(pk, dict) and pk.get("key"):
        from nacl import encoding, public
        box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
        for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
            value = env.get(name)
            if not value:
                print(f"  !! {name} missing from .env")
                continue
            enc = base64.b64encode(box.encrypt(value.encode())).decode()
            s2, r2 = proxy("PUT", f"/repos/{owner}/{a.repo}/actions/secrets/{name}",
                           {"encrypted_value": enc, "key_id": pk["key_id"]})
            print(f"  secret {name}: {s2}")
    else:
        print("public-key fetch failed:", st, str(pk)[:200])

    # ── pages ──────────────────────────────────────────────────────────────
    if not a.skip_pages:
        s3, r3 = proxy("POST", f"/repos/{owner}/{a.repo}/pages",
                       {"source": {"branch": "main", "path": "/docs"}})
        print("pages:", s3, (r3.get("html_url") if isinstance(r3, dict) else str(r3)[:120]))

    # ── dispatch ───────────────────────────────────────────────────────────
    if not a.no_dispatch:
        s4, r4 = proxy("POST",
                       f"/repos/{owner}/{a.repo}/actions/workflows/crawl.yml/dispatches",
                       {"ref": "main"})
        print("dispatch:", s4, str(r4)[:160])

    print(f"\nrepo: https://github.com/{owner}/{a.repo}")
    print(f"site: https://{owner}.github.io/{a.repo}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
