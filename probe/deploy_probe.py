#!/usr/bin/env python3
"""Deploy and trigger the eBay access probe via Composio (no PAT).

  python probe/deploy_probe.py --create    # create the public repo
  python probe/deploy_probe.py --push      # upload workflow + probe scripts
  python probe/deploy_probe.py --dispatch  # trigger the workflow by hand
  python probe/deploy_probe.py --runs      # list recent runs
  python probe/deploy_probe.py --logs ID   # fetch one run's job log (best effort)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER, REPO, BRANCH = "samarian5", "ebay-probe", "main"

FILES = [
    ("probe/workflow-probe.yml", ".github/workflows/probe.yml"),
    ("probe/probe_http.py", "probe/probe_http.py"),
    ("probe/probe_browser.py", "probe/probe_browser.py"),
]


def call(slug: str, payload: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        tmp = fh.name
    try:
        p = subprocess.run(["composio", "execute", slug, "-d", f"@{tmp}"],
                           capture_output=True, text=True, timeout=240)
    finally:
        os.unlink(tmp)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"successful": False, "error": (out or p.stderr)[:500]}


def remote_sha(path: str) -> str | None:
    r = call("GITHUB_GET_REPOSITORY_CONTENT",
             {"owner": OWNER, "repo": REPO, "path": path, "ref": BRANCH})
    data = r.get("data") or {}
    return data.get("sha") if isinstance(data, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--dispatch", action="store_true")
    ap.add_argument("--runs", action="store_true")
    ap.add_argument("--logs")
    a = ap.parse_args()

    if a.create:
        r = call("GITHUB_CREATE_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER",
                 {"name": REPO, "private": False,
                  "description": "eBay access probe: can a CI runner reach sold listings?"})
        print("create:", r.get("successful"), str(r.get("error") or "")[:300])
        data = r.get("data") or {}
        print("url:", data.get("html_url"), "| default_branch:", data.get("default_branch"))

    if a.push:
        for local, remote in FILES:
            src = ROOT / local
            payload = {"owner": OWNER, "repo": REPO, "path": remote, "branch": BRANCH,
                       "message": f"probe: {remote}",
                       "content": base64.b64encode(src.read_bytes()).decode()}
            sha = remote_sha(remote)
            if sha:
                payload["sha"] = sha
            r = call("GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS", payload)
            print(f"{'OK  ' if r.get('successful') else 'FAIL'} {remote} "
                  f"{str(r.get('error') or '')[:200]}")

    if a.dispatch:
        r = call("GITHUB_CREATE_A_WORKFLOW_DISPATCH_EVENT",
                 {"owner": OWNER, "repo": REPO, "workflow_id": "probe.yml",
                  "ref": BRANCH})
        print("dispatch:", r.get("successful"), str(r.get("error") or r.get("data"))[:300])

    if a.runs:
        r = call("GITHUB_LIST_WORKFLOW_RUNS_FOR_A_REPOSITORY",
                 {"owner": OWNER, "repo": REPO, "per_page": 5})
        runs = ((r.get("data") or {}).get("workflow_runs") or [])
        for w in runs:
            print(w.get("id"), w.get("name"), w.get("event"), w.get("status"),
                  w.get("conclusion"), w.get("created_at"))

    if a.logs:
        r = call("GITHUB_DOWNLOAD_WORKFLOW_RUN_LOGS",
                 {"owner": OWNER, "repo": REPO, "run_id": int(a.logs)})
        print(json.dumps(r)[:2000])

    return 0


if __name__ == "__main__":
    sys.exit(main())
