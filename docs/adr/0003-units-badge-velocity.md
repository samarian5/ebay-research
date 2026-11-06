# 0003 — Units-badge velocity replaces sell-through

**Status:** accepted

## Context

Paid tools rank niches on units moved, sell-through (`sold ÷ (sold + active)`) and
competition density. Sell-through needs sold listings with dates. ADR 0001 established
that surface is login-gated, so sell-through is uncollectible here.

## Decision

Rank on **units per day**, derived from the delta of eBay's cumulative `N+ sold` badge
between two laps divided by elapsed days. Lifetime units (the badge itself) is the ranking
key until a keyword has two laps. Watchers and attrition are displayed but never folded
into a composite score.

## Why

The badge is the only signed-out, publicly readable quantity that is monotonic with real
sales. A composite "opportunity score" would hide which signal drove a ranking, and the
signals have very different reliability.

## Consequences

- **The badge is a floor.** `63+ sold` means at least 63; eBay stops updating the exact
  number past a threshold. Units/day therefore under-counts, never over-counts.
- Coverage is thin in practice: 3–5 of 60 cards carry any badge on the sampled keywords.
  The **Volume Gate** (≥10 badged listings) marks everything else *unmeasured* rather than
  ranking noise.
- Velocity needs patience: the first lap can only show lifetime units, so the default sort
  has two states and the UI says which one it is using.
- Watchers appear on roughly 1 card in 60; collect them, never rank on them.
