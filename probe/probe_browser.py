#!/usr/bin/env python3
"""Tier 2 probe: real headed Chrome via SeleniumBase CDP mode, under Xvfb.

Same questions as tier 1, but with a browser that can execute eBay's SplashUI
challenge. Also extracts a few real rows so we learn the current card markup and
whether `Sold <date>` is printed per row.

Deliberately does NOT try to solve a captcha: the point is to find out whether a
clean headed profile from a runner IP is served data at all.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

OUT = Path("out")
OUT.mkdir(exist_ok=True)

HOME = "https://www.ebay.com/"
ACTIVE = ("https://www.ebay.com/sch/i.html?_nkw=funny+tshirt&_ipg=60&_pgn=1"
          "&LH_BIN=1&LH_ItemCondition=1000")
SOLD = ("https://www.ebay.com/sch/i.html?_nkw=funny+tshirt&_ipg=60&_pgn=1"
        "&LH_Sold=1&LH_Complete=1&LH_BIN=1&LH_ItemCondition=1000")

DATE_RE = re.compile(r"\b(?:Sold|Ended)\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})")

STATE_JS = """(() => {
  const t = document.title || '';
  const body = document.body ? document.body.innerText : '';
  const cards = document.querySelectorAll('li.s-card, li.s-item');
  const sample = [...cards].slice(0, 5).map(c => (c.innerText || '').replace(/\\s+/g, ' ').slice(0, 260));
  const count = (document.querySelector('.srp-controls__count-heading') || {}).innerText || '';
  const badges = (body.match(/[\\d,]+\\s*sold/gi) || []).slice(0, 5);
  return JSON.stringify({
    title: t.slice(0, 140),
    url: location.href.slice(0, 200),
    bodylen: body.length,
    pardon: /Pardon Our Interruption/i.test(body),
    captcha: /captcha|verify yourself|Security Measure/i.test(t + ' ' + body),
    signin: /Sign in or Register/i.test(t) || location.href.includes('signin'),
    cards: cards.length,
    sold_badges: badges.length,
    badge_sample: badges,
    count: count.replace(/\\s+/g, ' ').slice(0, 120),
    sample
  });
})()"""


def main() -> int:
    from seleniumbase import sb_cdp

    kwargs = dict(
        user_data_dir=os.environ.get("PROFILE_DIR", ".ebay-profile"),
        incognito=False,
        headless=False,
        ad_block=True,
        lang="en-US",
        tzone="America/New_York",
        sandbox=False,
    )
    if os.environ.get("BROWSER_BIN"):
        kwargs["browser_executable_path"] = os.environ["BROWSER_BIN"]

    print(json.dumps({"launching": {k: str(v) for k, v in kwargs.items()}}), flush=True)
    chrome = sb_cdp.Chrome(**kwargs)
    results = []

    for name, url in (("home", HOME), ("active", ACTIVE), ("sold", SOLD),
                      ("sold_only", SOLD.replace("&LH_Complete=1", "")),
                      ("complete_only", SOLD.replace("&LH_Sold=1", ""))):
        try:
            chrome.goto(url, timeout=90)
            # SplashUI needs a few seconds to redirect itself.
            for _ in range(3):
                time.sleep(3)
                st = json.loads(chrome.evaluate(STATE_JS))
                if not st["pardon"] and st["cards"] > 0:
                    break
            html = chrome.evaluate("document.documentElement.outerHTML") or ""
            (OUT / f"browser_{name}.html").write_text(html, encoding="utf-8")
            st["probe"] = name
            st["bytes"] = len(html)
            st["sold_dates"] = len(DATE_RE.findall(html))
            st["sold_badges"] = len(re.findall(r"\b[\d,]+\s*sold\b", html, re.I))
            print(json.dumps(st), flush=True)
            results.append(st)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"probe": name, "error": f"{type(e).__name__}: {e}"[:300]}), flush=True)
        time.sleep(5)

    reached = any(r.get("sold_dates", 0) > 0 for r in results)

    # The sold *search* needs a login. Item pages may still print a sold count and a
    # watcher count while logged out - that would be the whole demand signal.
    item_report = []
    try:
        html = (OUT / "browser_active.html").read_text(encoding="utf-8")
        ids = []
        for m in re.finditer(r"/itm/(\d{9,15})", html):
            if m.group(1) not in ids and m.group(1) != "123456":
                ids.append(m.group(1))
        for iid in ids[:3]:
            url = f"https://www.ebay.com/itm/{iid}"
            chrome.goto(url, timeout=90)
            time.sleep(4)
            txt = chrome.evaluate("document.body.innerText") or ""
            flat = re.sub(r"\s+", " ", txt)
            item_report.append({
                "probe": "item", "item_id": iid,
                "sold_text": (re.findall(r"[\d,]+\+?\s+sold", flat, re.I) or [])[:3],
                "watching": (re.findall(r"[\d,]+\s+(?:people (?:are|is) )?watch\w*", flat, re.I) or [])[:3],
                "available": (re.findall(r"[\d,]+\s+available|Last one|More than [\d,]+ available", flat, re.I) or [])[:3],
                "best_offer": "Best Offer" in flat,
                "title": (chrome.evaluate("document.title") or "")[:100],
            })
            print(json.dumps(item_report[-1]), flush=True)
            time.sleep(3)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"probe": "item", "error": f"{type(e).__name__}: {e}"[:200]}), flush=True)

    print(json.dumps({"tier": "browser", "reached_sold_rows": reached,
                      "items_probed": len(item_report),
                      "items_with_sold_text": sum(1 for i in item_report if i.get("sold_text"))}),
          flush=True)
    try:
        chrome.quit()
    except Exception:  # noqa: BLE001
        pass
    return 0 if reached else 2


if __name__ == "__main__":
    sys.exit(main())
