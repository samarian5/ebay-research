#!/usr/bin/env python3
"""Headless smoke test of the built SPA against live Supabase data.

  python scripts/verify_ui.py [--url http://127.0.0.1:8899/]

Exits non-zero if the board does not render keywords or the detail view does not
render listing cards. Prints console errors and saves screenshots to out/.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from pathlib import Path

OUT = Path("out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8899/")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    from seleniumbase import SB

    binary = os.environ.get("BROWSER_BIN") or glob.glob(
        os.path.expanduser("~/.cache/selenium/chrome/linux64/*/chrome"))
    if binary:
        binary = binary[0] if isinstance(binary, list) else binary
        print("chrome:", binary)

    errors: list[str] = []
    with SB(headless2=True, ad_block=True, binary_location=binary) as sb:
        sb.open(a.url)
        sb.wait_for_element_present(".kcard, #board-empty:not(.hidden)", timeout=25)
        time.sleep(1.5)

        cards = sb.find_elements(".kcard")
        print("keyword cards:", len(cards))
        print("sync:", sb.get_text("#sync"))
        if cards:
            print("first card:", " | ".join(sb.get_text(".kcard").splitlines()[:6]))
        sb.save_screenshot(str(OUT / "ui-board.png"))

        if not cards:
            print("FAIL: no keyword cards rendered")
            return 1

        sb.click(".kcard")
        sb.wait_for_element_present(".item, #items-empty:not(.hidden)", timeout=25)
        time.sleep(1.5)
        items = sb.find_elements(".item")
        print("listing cards:", len(items))
        if items:
            print("first item:", " | ".join(sb.get_text(".item").splitlines()[:5]))
        print("detail stats:", " | ".join(sb.get_text("#d-stats").splitlines()[:12]))
        sb.save_screenshot(str(OUT / "ui-detail.png"))

        errors = [e for e in sb.driver.get_log("browser") if e.get("level") == "SEVERE"]
        print("console errors:", len(errors))
        for e in errors[:5]:
            print("  ", e.get("message", "")[:200])

        if not items:
            print("FAIL: no listing cards rendered")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
