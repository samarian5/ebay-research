#!/usr/bin/env python3
"""Tier 1 probe: TLS-impersonated HTTP against eBay from a CI runner.

Answers, with evidence:
  * does a GitHub-hosted runner reach ebay.com at all (Akamai edge IP reputation)?
  * does a cookie warm-up get us past the 403?
  * does the active search page still carry `N sold` badges?
  * does the sold search page carry `Sold <date>` rows?

Writes raw HTML to out/ so the parser can be built against a real capture.
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
SOLD_BADGE_RE = re.compile(r"\b([\d,]+)\s*sold\b", re.I)


def verdict(name: str, url: str, resp) -> dict:
    body = resp.text if resp is not None else ""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
    low = body.lower()
    return {
        "probe": name,
        "url": url,
        "status": getattr(resp, "status_code", None),
        "bytes": len(body),
        "title": title,
        "pardon": "pardon our interruption" in low,
        "challenge": "splashui/challenge" in low or "captcha" in low,
        "access_denied": "access denied" in low,
        "cards_s_item": low.count("s-item"),
        "cards_s_card": low.count("s-card"),
        "sold_badges": len(SOLD_BADGE_RE.findall(body)),
        "sold_dates": len(DATE_RE.findall(body)),
        "result_count": (re.search(r'([\d,]+)\s*results?', body, re.I).group(1)
                         if re.search(r'([\d,]+)\s*results?', body, re.I) else None),
    }


def main() -> int:
    try:
        from curl_cffi import requests as creq
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"fatal": f"curl_cffi import failed: {e}"}))
        return 1

    session = creq.Session(impersonate="chrome")
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    })

    results = []
    for name, url, referer in (("home", HOME, None),
                               ("active", ACTIVE, HOME),
                               ("sold", SOLD, ACTIVE)):
        try:
            resp = session.get(url, timeout=40, allow_redirects=True,
                               headers={"Referer": referer} if referer else {})
            v = verdict(name, url, resp)
            (OUT / f"http_{name}.html").write_bytes(resp.content)
        except Exception as e:  # noqa: BLE001
            v = {"probe": name, "url": url, "error": f"{type(e).__name__}: {e}"[:300]}
        print(json.dumps(v))
        results.append(v)
        time.sleep(4)

    ok = any(r.get("sold_dates", 0) > 0 for r in results)
    print(json.dumps({"tier": "http", "reached_sold_rows": ok,
                      "egress_ip": os.environ.get("EGRESS_IP")}))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
