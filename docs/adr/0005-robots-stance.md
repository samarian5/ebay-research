# 0005 — Proceeding against a robots-disallowed search surface

**Status:** accepted · risk acknowledged

## Context

`https://www.ebay.com/robots.txt` disallows `/sch/` and `/sch/i.html?_nkw=` for all agents,
including Googlebot. The Active Surface this project depends on sits inside that path.

## Decision

Proceed anyway, at a deliberately low rate: ≤10 pages per keyword, 60 results per page,
about one lap per keyword every 48 hours, with ~4–6 s pacing between requests.

## Why

- The volume is personal research scale — tens of pages per day, not a crawl of the
  catalogue.
- No login, no account, no sale data, no circumvention of an access control: the pages are
  served to anonymous visitors.
- The alternative (official API) was excluded by the user's constraint, and the sold
  surface is login-walled regardless.

## Consequences

- eBay may block, throttle or change markup at any time; the parser is fixture-tested
  precisely so breakage is loud rather than silent.
- `403` is treated as a signal, not an obstacle to defeat: escalate once to a real browser,
  and if that fails, record the failure and stop. No captcha solving, no IP rotation, no
  proxy pools.
- If eBay objects, the correct response is to stop, not to escalate the technique.
