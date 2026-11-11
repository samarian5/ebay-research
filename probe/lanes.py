"""Access-lane probe.

Answers one question with evidence: which eBay surfaces respond from a GitHub
runner *right now*, and what markup do they return?

It is deliberately dumb and independent of the crawler's control flow: fetch six
URLs with the cheap lane, optionally retry the item page with the browser lane,
record status/block-detection/marker counts, and stash a small HTML sample of
each promising page in `eb_settings.lane_probe` so the parsers can be written
against the real markup instead of a guess.

Run:  python -m probe.lanes
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.fetch import Fetcher  # noqa: E402
from crawler.parse import is_blocked  # noqa: E402

ITEM_ID = "277260512268"  # a live Spigen listing used in earlier checks

TARGETS = [
    ("search_normal", "https://www.ebay.com/sch/i.html?_nkw=pokemon+card&_ipg=60"),
    ("search_rss", "https://www.ebay.com/sch/i.html?_nkw=pokemon+card&_rss=1&_ipg=60"),
    ("search_rss_p2", "https://www.ebay.com/sch/i.html?_nkw=pokemon+card&_rss=1&_ipg=60&_pgn=2"),
    ("item_page", f"https://www.ebay.com/itm/{ITEM_ID}"),
    ("sold_search", "https://www.ebay.com/sch/i.html?_nkw=pokemon+card&LH_Sold=1&LH_Complete=1&_ipg=60"),
    ("store_page", "https://www.ebay.com/str/spigen_inc"),
]

MARKERS = ("splashui", "error page", "captcha", "pardon our interruption",
           "access denied", "unusual traffic")

CARD_RE = re.compile(r'<li[^>]+class="[^"]*\bs-item\b[^"]*".*?</li>', re.S)
WATCH_RE = re.compile(r"([\d,]+)\s*(?:\+\s*)?(?:people are watching|watchers)", re.I)
SOLD_RE = re.compile(r"([\d,]+)\s*\+?\s*sold", re.I)
ID_RE = re.compile(r"/itm/(?:[^/\"?]*/)?(\d{9,15})")


def counts(html: str) -> dict:
    low = html.lower()
    return {
        "bytes": len(html),
        "title": (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1][:120],
        "s_cards": len(CARD_RE.findall(html)),
        "itm_ids": len(set(ID_RE.findall(html))),
        "watcher_hits": len(WATCH_RE.findall(html)),
        "sold_hits": len(SOLD_RE.findall(html)),
        "has_watching_phrase": "people are watching" in low,
        "markers": [m for m in MARKERS if m in low],
    }


def sample(html: str, name: str) -> dict:
    """Small, structure-preserving slices — enough to write a parser, cheap to store."""
    out = {}
    if name.startswith("search"):
        cards = CARD_RE.findall(html)
        out["card"] = cards[0][:6000] if cards else ""
    if name == "item_page":
        i = html.lower().find("people are watching")
        if i < 0:
            i = html.lower().find(" sold")
        out["region"] = html[max(0, i - 3000): i + 3000] if i > 0 else ""
        ids = list(dict.fromkeys(ID_RE.findall(html)))
        out["related_ids"] = ids[:25]
    if name == "sold_search":
        out["head"] = html[:1500]
    return out


def put(key: str, value) -> None:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/eb_settings?on_conflict=key"
    body = json.dumps([{"key": key, "value": value}]).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "apikey": os.environ["SUPABASE_SERVICE_KEY"],
        "Authorization": "Bearer " + os.environ["SUPABASE_SERVICE_KEY"],
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"[probe] wrote {key}: {r.status}", flush=True)


def main() -> None:
    res: dict = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "browser_bin": os.environ.get("BROWSER_BIN"), "lane1": {}, "lane2": {},
                 "samples": {}}
    f = Fetcher(allow_browser=False)
    keep: dict[str, str] = {}

    for name, url in TARGETS:
        t0 = time.time()
        try:
            status, html = f.http_get(url)
            reason = is_blocked(html, status)
        except Exception as e:  # noqa: BLE001
            status, html, reason = None, "", f"exception:{type(e).__name__}"
        rec = counts(html)
        rec.update(status=status, blocked=reason, secs=round(time.time() - t0, 1))
        res["lane1"][name] = rec
        print(f"[probe] {name:<14} {status} {rec['bytes']:>8}b cards={rec['s_cards']:<3} "
              f"ids={rec['itm_ids']:<3} warn={rec['watcher_hits']} sold={rec['sold_hits']} "
              f"blocked={reason}", flush=True)
        s = sample(html, name)
        if s:
            res["samples"][name] = s
        if name == "item_page" and len(html) > 10_000:
            keep[name] = html
        time.sleep(1.5)

    # Lane 2: only worth 90 s if lane 1 was challenged on the item page.
    if res["lane1"]["item_page"].get("blocked"):
        try:
            b = Fetcher(allow_browser=True)
            t0 = time.time()
            html = b.browser_get(f"https://www.ebay.com/itm/{ITEM_ID}")
            rec = counts(html)
            rec.update(secs=round(time.time() - t0, 1), blocked=is_blocked(html, 200))
            res["lane2"]["item_page"] = rec
            print(f"[probe] lane2 item_page {rec['bytes']}b blocked={rec['blocked']}", flush=True)
        except Exception as e:  # noqa: BLE001
            res["lane2"]["item_page"] = {"exception": f"{type(e).__name__}: {e}"[:400]}
            print(f"[probe] lane2 failed: {type(e).__name__}: {e}", flush=True)

    put("lane_probe", res)

    # Keep one real item page (gzipped, chunked) so parsers can be written and
    # tested against actual markup instead of a remembered layout.
    if keep.get("item_page"):
        import base64
        import gzip
        raw = keep["item_page"].encode()
        blob = base64.b64encode(gzip.compress(raw, 9)).decode()
        size = 40_000
        parts = [blob[i:i + size] for i in range(0, len(blob), size)]
        put("item_html_meta", {"item_id": ITEM_ID, "bytes": len(raw),
                               "parts": len(parts), "at": res["ran_at"]})
        for i, part in enumerate(parts):
            put(f"item_html_{i}", {"part": i, "b64": part})




if __name__ == "__main__":
    main()
