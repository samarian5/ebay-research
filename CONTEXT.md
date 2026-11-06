# CONTEXT — eBay Keyword Research Scraper

Domain glossary. Definitions only — no implementation, no specs, no roadmaps.

## Actor

**Researcher** — the single human who owns and reads this system. Uses it to decide which eBay
product niches to compete in, and which specific listings set the bar.

## Purpose

**Opportunity Research** — answering "what should I sell next?" for one Research Keyword at a
time. The output is a ranked shortlist of products worth making or sourcing, not a verdict on a
single item the Researcher already chose.

## Collection vocabulary

**Research Keyword** — a search phrase the Researcher wants data for (`funny tshirt`,
`personalized mug`). A first-class object with its own settings, not a free-text column on a row.

**In-Scope Market** — the part of a Research Keyword's results this system measures: listings with
condition New and buying format Buy It Now. Used goods and auctions are out of scope, because their
price formation and sales cadence differ from the inventory the Researcher would list. Every metric
in the system describes the In-Scope Market and nothing else.

**Keyword Run** — one bounded collection attempt for one Research Keyword. Has a start time, a
status, and a finite set of results. Re-running a keyword creates a new Keyword Run; it never
mutates a previous one.

**Depth** — how many search-result pages a Keyword Run walks for one Research Keyword. A ceiling,
not a promise: a narrow keyword serves fewer pages, and the Coverage Stop ends the run earlier.

**Coverage Stop** — the rule that ends a Keyword Run when a page yields no Listings not already
seen in that run. Depth is the ceiling; Coverage Stop is the brake.

## Objects

**Listing** — one eBay item, identified by its item number. Stable for that listing's life (a
Good-Til-Cancelled renewal keeps the number; a *Sell Similar* relist starts a new one). The only
permanent identity in the system.

**Product Cluster** — the same physical product or design, sold by one or more sellers under
different item numbers. Derived, not stored as identity: rebuilt on every import from eBay's catalog
product id when the item is branded, otherwise from perceptual image hash plus title tokens. Cluster
ids are not permanent and never act as a foreign key.

**Seller** — the eBay account behind a Listing. Belongs to many Listings; never a grouping used to
measure demand, because one seller's catalogue says nothing about which product sells.

**Ad Card** — a placeholder card eBay injects into search results (`Shop on eBay`, item number
`123456`). Carries no Listing and is dropped before any count.

## Field semantics

**Sold Badge** — the `N+ sold` counter printed on some search cards. A floor, never an exact count:
`63+ sold` means at least 63 units and may be more. Absent on most cards, so a blank badge means
"not shown", never "zero sold".

**Units** — the lower bound of lifetime units implied by a Sold Badge. Not a period figure: a listing
printed at `301+ sold` may have accumulated that over months.

**Units per Day** — the change in Units between two Snapshots of the same Listing, divided by
elapsed days. The system's only velocity measure. Undefined on a first observation, and undefined
when a badge never changes between laps — indistinguishable from slow selling.

**Watchers** — the `N watchers` count printed on some search cards. Intent, not purchase: a listing
can carry watchers and never sell.

**Attrition** — a Listing present in one Keyword Run and absent from the next, for the same Research
Keyword and page range. Evidence that it left the market — sold out or ended — with the cause
unobservable. The strongest demand signal available without a sold ledger.

**Volume Gate** — the rule that a Research Keyword is not ranked on thin evidence: it must show at
least 10 Listings carrying a Sold Badge, or it is reported as unmeasured rather than promising.

## Surfaces

**Active Surface** — eBay search restricted to listings on sale now (`/sch/i.html?_nkw=…`). The
system's only data source. Returns 60 cards per page and reaches GitHub-hosted runners in the US.

**Sold Surface** — eBay search restricted to completed sales (`LH_Sold=1&LH_Complete=1`). Shows sale
events with dates and final prices — but when signed out it **redirects to `signin.ebay.com`**, and
that sign-in page serves a captcha to datacenter IPs. Not reachable by this system; recorded here so
it is not re-litigated.

**Item Page** — one listing's own page (`/itm/<id>`). Reaches runners signed out and prints an exact
`N sold` for listings that have sold, plus quantity available. Consulted for a Shortlist, never for
bulk collection.

Both search surfaces are disallowed in `robots.txt` (`Disallow: /sch/`). The Active Surface is
therefore collected under the stance in ADR 0001 — personal research, low rate, no resale of data.
