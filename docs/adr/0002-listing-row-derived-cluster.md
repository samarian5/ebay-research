# 0002 — The stored row is a Listing; Product Clusters are derived and disposable

**Status:** accepted

## Context

One design sold by six sellers is one market signal but six eBay item numbers. Grouping
them is tempting as a first-class entity, but grouping logic is exactly the thing that
will change most often (image hashing, brand, title tokens).

## Decision

Store one row per **Listing** (`eb_id`, the eBay item number). A **Product Cluster** is
computed at read/import time from ePID when the listing is branded, else from a perceptual
hash of the thumbnail plus title tokens. Cluster ids are never foreign keys and are never
persisted as identity.

## Consequences

- Re-clustering is free to change: nothing references a cluster id.
- Clustering costs no extra requests — thumbnails are already in the search payload.
- GTC renewals keep the item number, so a renewed listing does not restart its history;
  *Sell Similar* mints a new `eb_id`, which appears as a new listing (correct: it is a new
  offer, even if the design is identical).
- Unbranded print-on-demand designs have no ePID, so image similarity carries the whole
  burden; the threshold is still untuned.
