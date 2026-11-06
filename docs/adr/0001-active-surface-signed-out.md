# 0001 — Active Surface only, signed out, from GitHub Actions

**Status:** accepted · amended 2026-11-06

## Context

The board follows demand per keyword. The obvious source is eBay's sold ledger
(`LH_Sold=1` / `LH_Complete=1`), which carries price, date and quantity — exactly what
sell-through needs.

## Decision

Collect only the **Active Surface** (live search results), signed out, from GitHub
Actions. No login, no session injection, no official API, no residential proxy.

## Why

- `LH_Sold=1` and `LH_Complete=1` both **302 to `signin.ebay.com`**, and the sign-in page
  serves `splashui/captcha`. Logged out, the sold ledger does not exist.
- `robots.txt` disallows the whole `/sch/` search surface, including for Googlebot.
- Proxies do not help: `r.jina.ai` returns eBay's error page; this laptop's residential IP
  gets `403` on every path.
- A GitHub runner (Azure, San Jose) returns **200, 1.57 MB, 62 cards** — the only vantage
  point proven to work.

## Consequences

- No sell-through, no sold prices, no sale dates. Velocity must be derived from the
  `N+ sold` badge instead (ADR 0003).
- The crawl is only viable inside CI. A laptop-hosted crawler is not a fallback.

## Amendment — 2026-11-06, measured access reality

The first four live laps changed the risk picture:

1. **The cheap lane is blocked roughly half the time, not rarely.** `curl_cffi` returned
   `403` on 4 of 10 pages in one lap and 4 of 4 in another. The browser lane (SeleniumBase
   CDP under Xvfb) recovered every one of those pages. Escalation is therefore a normal
   path, not an exception — cost per lap must budget for it.
2. **A blocked page can come back as an empty page, not a detectable block.** One browser
   escalation returned a page with zero `li.s-card` elements and no interstitial marker.
   Treated as "end of results", it silently truncated a lap at page 6 of 10 and advanced
   the queue past pages 7–10. Now: a zero-card page is retried once, and if still empty it
   is counted as a page error; the queue resumes at the first unread page instead of
   skipping the gap.
3. **The CDP lane can die mid-lap** (`ProtocolException` after three pages). A single page
   failure must not discard the keyword's lap: page errors are caught per page, three
   consecutive failures stop the run, and any non-clean stop resumes at the failed page.
4. **Supabase cannot crawl.** `pg_net` from the project's AWS address returns `403`
   (1,738-byte error page) even with a full Chrome header set, while `example.com` returns
   200 — so the block is IP reputation, not headers. This killed the "no GitHub, database
   does the fetching" option outright.
