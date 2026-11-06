#!/usr/bin/env python3
"""Headless test of the write path: add a keyword through the UI form.

Needs EB_WRITE_TOKEN in the environment (read from .env by the caller); the token is
typed into the form the way a user would, so this also proves the browser -> Supabase
CORS path works.

  set -a; . ./.env; set +a; python scripts/verify_add.py
"""
from __future__ import annotations

import glob
import os
import sys
import time


def main() -> int:
    token = os.environ.get("EB_WRITE_TOKEN", "")
    if not token:
        return print("EB_WRITE_TOKEN not set") or 2
    kw = os.environ.get("TEST_KEYWORD", "test keyword do not run")
    url = os.environ.get("TEST_URL", "http://127.0.0.1:8899/")

    from seleniumbase import SB

    binary = glob.glob(os.path.expanduser("~/.cache/selenium/chrome/linux64/*/chrome"))
    with SB(headless2=True, ad_block=True,
            binary_location=binary[0] if binary else None) as sb:
        sb.open(url)
        sb.wait_for_element_present(".kcard, #board-empty:not(.hidden)", timeout=25)
        sb.click('button.tab[data-view="add"]')
        sb.type('input[name="keyword"]', kw)
        sb.type('input[name="token"]', token)
        sb.click('#add-form button[type="submit"]')
        time.sleep(3)
        msg = sb.get_text("#add-msg")
        print("form message:", msg)
        managed = sb.get_text("#manage")
        print("listed in manage:", kw in managed)
        sb.save_screenshot("out/ui-add.png")
        ok = "queued" in msg.lower() and kw in managed
        if not ok:
            print("FAIL: keyword not accepted through the UI")
            return 1

        # clean up so repeated runs stay green
        sb.click('button.tab[data-view="board"]')
        time.sleep(1)
        print("board cards:", len(sb.find_elements(".kcard")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
