# eBay Opportunity Board

Answers "what should I sell next on eBay?" per keyword, from data a signed-out crawler can
actually reach. Sibling of `/home/pc/etsy-scraper`, sharing its shape — GitHub Actions,
Supabase queue, static web board — and none of its sealed-artifact machinery.

**Status:** stack built and wired. Schema live in Supabase, parser tested against a real
capture, queue + ingest + anon-read verified end to end, web board rendering live rows.
**One thing is not done: the crawl has never run in GitHub Actions from this repo**, because
this machine has no GitHub credentials (no `gh`, no SSH key, no PAT). Everything is packaged
for one command once a token exists.

## What was proven before designing anything

Probe repo: `samarian5/ebay-probe` (public, throwaway). Two failing runs are in the record
alongside the passing ones.

| Probe | Result |
|---|---|
| Plain `curl` from this laptop (HU residential) | 403 on every path |
| `curl_cffi` from this laptop | 403 on every path |
| Headless Chrome from this laptop | `splashui/challenge` |
| `curl_cffi` from a GitHub runner (Azure, San Jose) | search **200**, 1.57 MB, 62 cards |
| SeleniumBase CDP headed + Xvfb from a runner | **200**, 62 cards, "1,200,000+ results" |
| Sold / completed search from a runner | **302 → signin.ebay.com** (`LH_Sold=1`, `LH_Complete=1`) |
| Sign-in page | `splashui/captcha` |
| Item pages from a runner | 200; exact `5 sold` on one; `More than 10 available`; no watchers |
| `r.jina.ai` proxy reader (2026-11-06 retest) | eBay **Error Page** — third-party proxies do not help |

So: the sold ledger is login-gated, the laptop is blocked outright, and the Active Surface is
reachable from the mandated environment. That single table is why this design looks the way
it does.

## How "best selling" is decided (the paid-tool logic, rebuilt)

Paid tools (Terapeak, Zik, SellerAmp) rank niches on *units moved*, *sell-through* and
*competition density*. Sell-through needs sold listings with dates, which eBay gates behind a
login, so this board substitutes signals it can actually observe and never mixes them into a
fake composite score:

| Signal | Source | How to read it |
|---|---|---|
| **Units per day** | delta of the `63+ sold` badge between two laps ÷ elapsed days | the headline. Needs two laps. A badge is a **floor**, so this under-counts |
| **Lifetime units** | the badge itself | ranking key until the second lap exists |
| **Badge coverage** | badged ÷ stored listings | how much of the niche is proven-to-sell at all |
| **Attrition** | share of last lap's listings absent this lap | high attrition = churn, listings die young |
| **Median price** | price of every stored listing | where the niche sits; pair with units, never alone |
| **Watchers** | `12 watchers` when eBay shows it | sparse (~1 in 60); collect, don't rank on it |

**Volume gate:** a keyword with fewer than 10 badged listings is labelled *unmeasured* and is
never ranked. On the sampled page only 3 of 60 cards carried a badge, so most keywords will
start unmeasured — that is the honest answer, not a bug.

## Decisions of record

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-active-surface-signed-out.md) | Active Surface only, signed out, from GitHub Actions. No login, no session injection, no API |
| [0002](docs/adr/0002-listing-row-derived-cluster.md) | Listing is the stored row; product cluster derived and disposable |
| [0003](docs/adr/0003-units-badge-velocity.md) | Units-badge velocity replaces sell-through; two-state leaderboard default |
| [0004](docs/adr/0004-public-repo-db-queue.md) | Public repo, queue in Postgres, no sealed artifacts |
| [0005](docs/adr/0005-robots-stance.md) | Proceeds against a robots-disallowed search surface, personal research, low rate |

Glossary: [`CONTEXT.md`](CONTEXT.md).

## Shape

```
every 6 h (cheap tick) ──► GitHub Actions
                              │  eb_claim_slice()        ← due-ness lives in Postgres
                              ▼
                        curl_cffi  ──(403/challenge)──►  SeleniumBase CDP + Xvfb
                              │
                              ▼
                 up to 10 pages × 60 cards per keyword ──►  eb_ingest_batch()
                              │
                              ▼
                    Supabase Postgres  ──►  static board (anon key, RLS read-only)
                    eb_ tables              keywords → grid, add/pause/remove
```

- **Scope:** condition New, buying format Buy It Now. One market, never mixed with used or
  auctions.
- **Rate:** 20 keywords × 10 pages = 200 loads per lap, ~25 min, ~375 CI minutes/month.
- **Storage:** ~12k observations/lap; `eb_prune(7)` keeps a week of full history then one row
  per listing per day, so the free 500 MB tier holds years.

## What is built

| Path | What it does | Verified |
|---|---|---|
| `db/supabase/0001_eb_init.sql` | 6 tables, 8 RPCs, board view, pruning, token-gated writes, RLS | applied to the live project |
| `crawler/parse.py` | card parser: badge floor, watchers, price ranges, ad-card drop | 8 pytest tests against a real capture |
| `crawler/fetch.py` | curl_cffi lane + SeleniumBase escalation + block detector | block detector unit-tested; lanes proven by the probe |
| `crawler/crawl.py` | claim → crawl → ingest → finish, coverage stop when a page yields nothing new | ingest + queue exercised via RPC |
| `crawler/supa.py` | REST client for the RPCs | live |
| `web/` | mobile-first board: leaderboard, keyword grid, add/pause/remove | headless run: 2 keywords, 60 cards, 0 severe console errors |
| `.github/workflows/crawl.yml` | 6-hourly tick, Xvfb, artifact upload, step summary | **never run** |
| `scripts/push_repo.py` | repo + Pages + Actions secrets from a PAT, no git needed | dry-run only |

## Everyday commands

```bash
PY=/home/pc/etsy-scraper/.venv/bin/python
$PY scripts/apply_schema.py             # (re)apply SQL, idempotent
$PY scripts/seed_fixture.py --file tests/fixtures/real/search_active_2026-11-06.html.gz
$PY scripts/deploy_pages.py --serve     # build docs/ and serve on :8787
$PY scripts/verify_ui.py                # headless smoke test + screenshots
$PY -m pytest tests/ -q                 # parser tests
$PY crawler/crawl.py --keyword "funny tshirt" --pages 2 --no-db   # direct mode
echo "$PAT" > ~/.github-token && chmod 600 ~/.github-token
$PY scripts/push_repo.py                # publish + enable Pages + set secrets
```

## Open questions

1. **Badge coverage on real keywords.** 3 of 60 cards on the sampled page. Measure across 10
   keywords before trusting the leaderboard; if it stays this thin, depth 10 is not enough.
2. **Attrition under page shuffling.** Best Match reorders between laps, so a listing missing
   from page 6 may have moved to page 4. The board compares run-level sets, not page numbers,
   which is right — but the noise floor is still unmeasured.
3. **Seller competition.** Cards in the capture carry no seller name, so distinct-seller count
   (what paid tools call competition) needs item-page enrichment. Not built.
4. **Schedule reliability.** GitHub's scheduler never fired for the Etsy repo on this same
   account. Verify `event=schedule` in the run list before trusting the 6-hour tick.
5. **Cluster threshold.** Image-hash similarity needs tuning against real captures; not built,
   and not needed for any shipped metric.
