#!/usr/bin/env python3
"""One-shot: start a GitHub device-flow login and save the token to ~/.github-token.

  python scripts/device_login.py            # start + poll for up to 15 min
  python scripts/device_login.py --poll     # only poll an existing /tmp/ghdevice.json

Prints the user code; the token is written to ~/.github-token (mode 600) and never
echoed to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

CLIENT_ID = "178c6fc778ccc68e1d6a"  # GitHub CLI's public OAuth app id
SCOPE = "repo workflow read:user"
DEVICE_FILE = Path("/tmp/ghdevice.json")
TOKEN_FILE = Path.home() / ".github-token"


def start() -> dict:
    r = requests.post("https://github.com/login/device/code",
                      headers={"Accept": "application/json"},
                      data={"client_id": CLIENT_ID, "scope": SCOPE}, timeout=30)
    r.raise_for_status()
    d = r.json()
    d["started_at"] = time.time()
    DEVICE_FILE.write_text(json.dumps(d))
    return d


def poll(d: dict, budget_s: int = 900) -> bool:
    interval = max(5, int(d.get("interval", 5)))
    deadline = d.get("started_at", time.time()) + min(budget_s, int(d.get("expires_in", 900)))
    while time.time() < deadline:
        time.sleep(interval)
        r = requests.post("https://github.com/login/oauth/access_token",
                          headers={"Accept": "application/json"},
                          data={"client_id": CLIENT_ID,
                                "device_code": d["device_code"],
                                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                          timeout=30)
        body = r.json()
        err = body.get("error")
        if body.get("access_token"):
            TOKEN_FILE.write_text(body["access_token"].strip() + "\n")
            os.chmod(TOKEN_FILE, 0o600)
            print("AUTHORIZED: token saved to", TOKEN_FILE)
            return True
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        print("failed:", err, body.get("error_description", ""))
        return False
    print("expired before authorization")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--budget", type=int, default=900)
    a = ap.parse_args()
    if a.poll:
        d = json.loads(DEVICE_FILE.read_text())
    else:
        d = start()
        print("URL:  ", d["verification_uri"])
        print("CODE: ", d["user_code"])
        print("expires in", d["expires_in"], "seconds", flush=True)
    return 0 if poll(d, a.budget) else 1


if __name__ == "__main__":
    sys.exit(main())
