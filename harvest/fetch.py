"""Fetch lane for eBay through Google's translate proxy.

Why this exists (2026-11-11): eBay answers 403 to this host and to GitHub
runners on every `/sch/` and `/itm/` path — verified three times in one evening
(`eb_settings.lane_probe`). Asking Google to fetch the page instead
(`www-ebay-com.translate.goog`) returns the real 2.4 MB search page and the real
1.1 MB item page, with watcher counts intact.

The proxy is a transport, not a scraper: no captcha solving, no IP rotation, no
login. Requests are paced, retried twice, and a challenge is reported rather
than defeated (ADR 0005).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

import requests

HOME = "https://www.ebay.com/"
PROXY_BASE = "https://www-ebay-com.translate.goog"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TR = "_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"
BLOCK_MARKERS = ("error page", "splashui", "pardon our interruption", "captcha")


@dataclass
class Fetched:
    url: str
    status: int | None
    html: str
    lane: str            # direct | proxy | failed
    blocked: str | None = None
    secs: float = 0.0


def proxied(url: str) -> str:
    """Rewrite an ebay.com URL onto the translate proxy."""
    if "translate.goog" in url:
        return url
    path = url.split("ebay.com", 1)[-1] or "/"
    return f"{PROXY_BASE}{path}{'&' if '?' in path else '?'}{TR}"


def looks_blocked(html: str, status: int | None) -> str | None:
    if status == 403:
        return "http_403"
    if status and status >= 400:
        return f"http_{status}"
    if len(html) < 20_000:
        low = html.lower()
        if any(m in low for m in BLOCK_MARKERS):
            return "challenge_page"
        return "short_page"
    return None


class Fetcher:
    def __init__(self, direct_first: bool = True, delay: tuple[float, float] = (1.2, 2.8)):
        self.direct_first = direct_first
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

    def _sleep(self) -> None:
        time.sleep(random.uniform(*self.delay))

    def _get(self, url: str) -> tuple[int | None, str]:
        r = self.session.get(url, timeout=60, allow_redirects=True)
        return r.status_code, r.text

    def get(self, url: str, retries: int = 2) -> Fetched:
        t0 = time.time()
        last: Fetched | None = None

        lanes = [("direct", url), ("proxy", proxied(url))] if self.direct_first else [("proxy", proxied(url))]
        for lane, target in lanes:
            try:
                status, html = self._get(target)
            except Exception as e:  # noqa: BLE001
                status, html = None, f"<exception {type(e).__name__}>"
            reason = looks_blocked(html, status)
            last = Fetched(target, status, html, lane, reason, round(time.time() - t0, 1))
            if not reason:
                return last
            self._sleep()

        for attempt in range(retries):
            self._sleep()
            try:
                status, html = self._get(proxied(url))
            except Exception as e:  # noqa: BLE001
                status, html = None, f"<exception {type(e).__name__}>"
            reason = looks_blocked(html, status)
            last = Fetched(proxied(url), status, html, "proxy", reason, round(time.time() - t0, 1))
            if not reason:
                return last
            time.sleep(3 * (attempt + 1))
        return last or Fetched(url, None, "", "failed", "no_attempt")
