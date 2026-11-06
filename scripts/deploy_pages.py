#!/usr/bin/env python3
"""Build the static site: writes web/config.js and mirrors web/ into docs/.

  python scripts/deploy_pages.py            # build docs/ for GitHub Pages
  python scripts/deploy_pages.py --serve    # build, then serve on :8787 for a LAN/tunnel preview

The anon key is public by design (RLS grants SELECT only); the write token is never
written to config.js.
"""
from __future__ import annotations

import argparse
import http.server
import os
import shutil
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DOCS = ROOT / "docs"


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


def build() -> Path:
    env = load_env()
    url = env.get("SUPABASE_URL", "")
    anon = env.get("SUPABASE_ANON_KEY", "")
    if not url or not anon:
        raise SystemExit("SUPABASE_URL / SUPABASE_ANON_KEY missing")

    (WEB / "config.js").write_text(
        "window.EB_CONFIG = {\n"
        f"  url: {url!r},\n"
        f"  anon: {anon!r},\n"
        "};\n"
    )

    if DOCS.exists():
        # Never rmtree the whole folder: docs/adr/ holds the decision records.
        # Only the built web assets are replaced.
        pass
    DOCS.mkdir(parents=True, exist_ok=True)
    for f in ("index.html", "style.css", "app.js", "config.js"):
        shutil.copy2(WEB / f, DOCS / f)
    (DOCS / ".nojekyll").write_text("")
    print(f"built {DOCS} ({len(list(DOCS.iterdir()))} entries)")
    return DOCS


def serve(directory: Path, port: int = 8787) -> None:
    os.chdir(directory)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
        print(f"serving {directory} on http://0.0.0.0:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    out = build()
    if a.serve:
        serve(out, a.port)
