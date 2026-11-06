"""Two-lane fetcher for eBay search pages.

Lane 1 (cheap): curl_cffi with a Chrome TLS fingerprint and a cookie warm-up.
Verified 2026-11-06 to return the full 1.5 MB search page from a GitHub runner.

Lane 2 (fallback): SeleniumBase CDP mode — headed Chrome under Xvfb, warm profile
— which can execute eBay's SplashUI challenge. Imported lazily so the cheap lane
never pays for Chrome.

The fetcher never solves captchas and never rotates IPs: a challenge that survives
one browser attempt is reported, not defeated (ADR 0005).
"""
from __future__ import annotations

import os
import random
import time

from .parse import is_blocked

HOME = "https://www.ebay.com/"
SEARCH = ("https://www.ebay.com/sch/i.html?_nkw={q}&_ipg=60&_pgn={page}"
          "&LH_BIN=1&LH_ItemCondition=1000")
UA_LANG = "en-US,en;q=0.9"


class Fetcher:
    def __init__(self, profile_dir: str = ".ebay-profile", allow_browser: bool = True,
                 save_html: int = 0):
        self.profile_dir = profile_dir
        self.allow_browser = allow_browser
        self.save_html = save_html
        self.session = None
        self.browser = None
        self.challenges = 0
        self.tier_used: list[str] = []
        self.saved = 0

    # -- lane 1 ------------------------------------------------------------
    def _session(self):
        if self.session is None:
            from curl_cffi import requests as creq
            self.session = creq.Session(impersonate="chrome")
            self.session.headers.update({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": UA_LANG,
                "Upgrade-Insecure-Requests": "1",
            })
            try:  # cookie warm-up; a 403 here is normal and harmless
                self.session.get(HOME, timeout=30, allow_redirects=True)
            except Exception:  # noqa: BLE001
                pass
        return self.session

    def http_get(self, url: str) -> tuple[int | None, str]:
        s = self._session()
        r = s.get(url, timeout=45, allow_redirects=True,
                  headers={"Referer": HOME})
        return r.status_code, r.text

    # -- lane 2 ------------------------------------------------------------
    def _browser(self):
        if self.browser is None:
            from seleniumbase import sb_cdp
            kwargs = dict(
                user_data_dir=self.profile_dir,
                incognito=False,
                headless=False,
                ad_block=True,
                lang="en-US",
                tzone="America/New_York",
                sandbox=bool(os.environ.get("CI")),
            )
            if os.environ.get("BROWSER_BIN"):
                kwargs["browser_executable_path"] = os.environ["BROWSER_BIN"]
            self.browser = sb_cdp.Chrome(**kwargs)
        return self.browser

    def browser_get(self, url: str) -> str:
        b = self._browser()
        b.goto(url, timeout=90)
        for _ in range(3):
            time.sleep(3)
            html = b.evaluate("document.documentElement.outerHTML") or ""
            if not is_blocked(html):
                return html
        return html

    # -- public ------------------------------------------------------------
    def search_page(self, keyword: str, page: int) -> tuple[str, str]:
        """Return (html, tier) for one search page, escalating when needed."""
        url = SEARCH.format(q=keyword.replace(" ", "+"), page=page)
        reason = None
        try:
            status, html = self.http_get(url)
            reason = is_blocked(html, status)
        except Exception as e:  # noqa: BLE001
            html, reason = "", f"exception:{type(e).__name__}"

        if not reason:
            self.tier_used.append("http")
            self._maybe_save(keyword, page, html)
            return html, "http"

        if not self.allow_browser:
            raise RuntimeError(f"blocked on page {page}: {reason}")

        self.challenges += 1
        print(f"[fetch] page {page} blocked ({reason}) -> browser lane", flush=True)
        html = self.browser_get(url)
        still = is_blocked(html)
        if still:
            raise RuntimeError(f"browser lane also blocked on page {page}: {still}")
        self.tier_used.append("browser")
        self._maybe_save(keyword, page, html)
        return html, "browser"

    def _maybe_save(self, keyword: str, page: int, html: str) -> None:
        if self.save_html and self.saved < self.save_html:
            import gzip
            from pathlib import Path
            out = Path("out/raw")
            out.mkdir(parents=True, exist_ok=True)
            slug = "".join(c if c.isalnum() else "-" for c in keyword)[:40]
            path = out / f"{slug}_p{page}.html.gz"
            path.write_bytes(gzip.compress(html.encode("utf-8", "replace")))
            self.saved += 1

    def close(self) -> None:
        if self.browser is not None:
            try:
                self.browser.quit()
            except Exception:  # noqa: BLE001
                pass


def pace(base: float = 3.5, jitter: float = 2.0) -> None:
    time.sleep(base + random.random() * jitter)
