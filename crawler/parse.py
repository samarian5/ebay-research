"""Parse eBay Active Surface search pages.

Markup verified against a real capture (tests/fixtures/real/search_active_2026-11-06.html.gz):

  li.s-card                     one card; data-listingid is the item number
  .s-card__title                title (+ "Opens in a new window or tab", stripped)
  .s-card__subtitle-row         "Size M", "Brand New"
  .s-card__attribute-row        price ("$21.95 to $25.60"), "Buy It Now",
                                "Free delivery", "Located in United States",
                                "12 watchers", "63+ sold", "More than 10 available"
  .s-card__footer               "derosnopS" == "Sponsored" reversed -> is_sponsored

The sold badge is a *floor*: "63+ sold" means at least 63 units (ADR 0003).
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

ITEM_RE = re.compile(r"/itm/(\d{6,16})")
DECORATION_RE = re.compile(r"\s*Opens in a new window or tab\s*$", re.I)
SOLD_RE = re.compile(r"([\d.,]+)\s*\+?\s*sold\b", re.I)
WATCH_RE = re.compile(r"([\d.,]+)\s*\+?\s+watch", re.I)
AVAIL_RE = re.compile(r"(more than\s+[\d,]+\s+available|last one|[\d,]+\s+available)", re.I)
MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
CONDITIONS = ("brand new", "new (other)", "new other", "open box", "used", "refurbished")
AD_MARKERS = ("shop on ebay",)


def _num(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(float(raw.replace(",", "").replace(".", "")))
    except ValueError:
        return None


def _money(raw: str | None) -> float | None:
    if not raw:
        return None
    m = MONEY_RE.search(raw.replace(",", ""))
    return float(m.group(1)) if m else None


def parse_card(card) -> dict[str, Any] | None:
    title_el = card.select_one(".s-card__title")
    title = DECORATION_RE.sub("", title_el.get_text(" ", strip=True)) if title_el else ""
    if not title or title.strip().lower() in AD_MARKERS:
        return None

    eb_id = (card.get("data-listingid") or "").strip()
    if not eb_id:
        link = card.select_one('a[href*="/itm/"]')
        m = ITEM_RE.search(link["href"]) if link and link.get("href") else None
        eb_id = m.group(1) if m else ""
    if not eb_id or eb_id == "123456":
        return None

    rows = [r.get_text(" ", strip=True) for r in card.select(".s-card__attribute-row, .s-card__subtitle-row")]
    flat = " | ".join(rows)

    price_row = next((r for r in rows if "$" in r), "")
    prices = [float(p.replace(",", "")) for p in MONEY_RE.findall(price_row.replace(",", ""))]
    price, price_max = (min(prices), max(prices)) if prices else (None, None)

    condition = next((r for r in rows if r.lower().startswith(CONDITIONS)), None)
    buying = next((r for r in rows if re.search(r"buy it now|best offer|auction", r, re.I)), None)
    delivery = next((r for r in rows if re.search(r"delivery|shipping", r, re.I)), None)
    location = next((r for r in rows if r.lower().startswith("located in")), None)
    qty_raw = next((r for r in rows if AVAIL_RE.search(r)), None)

    sold_m = SOLD_RE.search(flat)
    watch_m = WATCH_RE.search(flat)

    img = card.select_one("img.s-card__image") or card.select_one("img")
    image_url = ""
    if img:
        image_url = img.get("data-defer-load") or img.get("src") or ""
        image_url = image_url.replace("/s-l140.", "/s-l500.")

    footer = card.select_one(".s-card__footer")
    footer_text = footer.get_text(" ", strip=True) if footer else ""
    # NB: every card carries a reversed "derosnopS" decoration, so that string is
    # NOT a sponsorship flag. Only an explicit badge counts.
    is_sponsored = bool(card.select_one(".s-card__ad-badge, [data-ad-label]"))

    qty = None
    if qty_raw:
        if qty_raw.lower().startswith("more than"):
            qty = _num(AVAIL_RE.search(qty_raw).group(1).split()[-2]) if AVAIL_RE.search(qty_raw) else 10
        elif qty_raw.lower() == "last one":
            qty = 1
        else:
            qty = _num(AVAIL_RE.search(qty_raw).group(1)) if AVAIL_RE.search(qty_raw) else None

    return {
        "eb_id": eb_id,
        "url": f"https://www.ebay.com/itm/{eb_id}",
        "title": title,
        "seller": None,
        "seller_feedback": None,
        "price_usd": price,
        "price_max_usd": price_max,
        "image_url": image_url,
        "condition": condition,
        "buying_format": buying,
        "shipping_text": delivery,
        "free_shipping": bool(delivery and re.search(r"free", delivery, re.I)),
        "location": location,
        "quantity_text": qty_raw,
        "quantity_avail": qty,
        "units_badge": _num(sold_m.group(1)) if sold_m else None,
        "watchers": _num(watch_m.group(1)) if watch_m else None,
        "is_sponsored": is_sponsored,
    }


def parse_search_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pos, card in enumerate(soup.select("li.s-card, li.s-item"), start=1):
        row = parse_card(card)
        if not row or row["eb_id"] in seen:
            continue
        row["position"] = pos
        seen.add(row["eb_id"])
        out.append(row)
    return out


def parse_result_count(html: str) -> str | None:
    """The count is split across spans: `1,200,000` + `+ results for`."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"([\d,]{3,})\s*\+?\s*results?\s+for\b", text, re.I)
    return m.group(1) if m else None


def is_blocked(html: str, status: int | None = None) -> str | None:
    """Return a reason string when the page is not real search results."""
    if status in (403, 429, 503):
        return f"http_{status}"
    low = (html or "").lower()
    if "pardon our interruption" in low:
        return "interstitial"
    if "splashui/challenge" in low or "/splashui/captcha" in low:
        return "challenge"
    if "signin.ebay.com" in low and "sign in or register" in low:
        return "signin"
    if 'li class="s-card' not in html and "<title>error page" in low:
        return "error_page"
    return None
