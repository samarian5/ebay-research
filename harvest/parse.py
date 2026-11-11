"""Parsers for the two eBay surfaces this pipeline uses.

Written against real pages captured on 2026-11-11 (see `probe/samples/`), not
from memory: search cards carry `data-listingid`, `.s-card__title`,
`.s-card__price` and `s-card__attribute-row` spans; item pages carry
"<n> people are watching this", "N sold" next to "More than 10 available", the
seller block, and the related-items carousel.

Parsing rules that matter for scoring:
* a sold badge ending in "+" (or "sold" without a count) is a FLOOR → `units_is_floor`
* a missing watcher count is None, never 0
* "More than 10 available" is stored as text plus a floor of 10
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

ITEM_RE = re.compile(r"/itm/(?:[^/\"?]*/)?(\d{9,15})")
MONEY_RE = re.compile(r"US\s*\$?\s*([\d,]+(?:\.\d{2})?)|\$\s*([\d,]+(?:\.\d{2})?)")
WATCH_RE = re.compile(r"([\d,]+)\s*\+?\s*(?:people are watching(?: this)?|watchers?)", re.I)
SOLD_RE = re.compile(r"([\d,]+)\s*(\+)?\s*sold", re.I)
QTY_RE = re.compile(r"Quantity:\s*(More than\s+[\d,]+|[\d,]+)\s+available|(Last one)", re.I)

PCT_RE = re.compile(r"([\d.]+)\s*%\s*positive", re.I)
ITEMS_SOLD_RE = re.compile(r"([\d,.]+)\s*([KM])?\s*items sold", re.I)


def _t(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _money(text: str) -> float | None:
    m = MONEY_RE.search(text or "")
    if not m:
        return None
    raw = (m.group(1) or m.group(2)).replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _num(text: str) -> int | None:
    m = re.search(r"([\d,]+)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


def _magnitude(raw: str, suffix: str | None) -> int | None:
    try:
        v = float(raw.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        v *= 1000 if suffix.upper() == "K" else 1_000_000
    return int(v)


def parse_search(html: str, keyword: str | None = None) -> list[dict[str, Any]]:
    """One dict per organic search card."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for li in soup.select("li.s-card[data-listingid]"):
        item_id = (li.get("data-listingid") or "").strip()
        if not item_id.isdigit():
            continue
        title = _t(li.select_one(".s-card__title"))
        title = re.sub(r"\s*Opens in a new window or tab\s*$", "", title, flags=re.I)
        if not title or title.lower().startswith("shop on ebay"):
            continue
        attrs = [_t(a) for a in li.select(".s-card__attribute-row")]
        blob = " | ".join(attrs)
        price = _money(_t(li.select_one(".s-card__price")) or blob)
        watch = WATCH_RE.search(blob)
        sold = SOLD_RE.search(blob)
        qty = QTY_RE.search(blob)
        img = li.select_one("img")
        rows.append({
            "item_id": item_id,
            "url": f"https://www.ebay.com/itm/{item_id}",
            "title": title,
            "niche": keyword,
            "price_usd": price,
            "currency": "USD" if price is not None else None,
            "condition": _t(li.select_one(".s-card__subtitle")) or None,
            "buying_format": "BIN" if "buy it now" in blob.lower() else ("auction" if "bid" in blob.lower() else None),
            "watchers": int(watch.group(1).replace(",", "")) if watch else None,
            "units_sold": int(sold.group(1).replace(",", "")) if sold else None,
            "units_is_floor": bool(sold and sold.group(2)) if sold else False,
            "quantity_text": qty.group(0) if qty else None,
            "quantity_n": _num(qty.group(0)) if qty else None,
            "free_shipping": "free" in blob.lower() and ("delivery" in blob.lower() or "shipping" in blob.lower()),
            "shipping_text": next((a for a in attrs if "delivery" in a.lower() or "shipping" in a.lower()), None),
            "location": next((a.split("Located in")[-1].strip() for a in attrs if "located in" in a.lower()), None),
            "image_url": (img.get("src") if img else None),
            "source": "search",
            "state": "new",
        })
    return rows


def parse_item(html: str, item_id: str | None = None) -> dict[str, Any]:
    """Everything the item page states, plus the related-item ids."""
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    item_id = item_id or item_id_of(html)

    watch = WATCH_RE.search(text)
    sold = SOLD_RE.search(text)
    qty = QTY_RE.search(text)
    pct = PCT_RE.search(text)
    sold_items = ITEMS_SOLD_RE.search(text)

    title = _t(soup.select_one("h1.x-item-title__mainTitle")) or _t(soup.select_one("h1"))
    price_el = soup.select_one(".x-price-primary, [data-testid='x-price-primary']")
    price = _money(_t(price_el)) if price_el else None
    if price is None:
        ld = re.search(r'"price"\s*:\s*"?([\d.]+)', html)
        price = float(ld.group(1)) if ld else None

    seller_el = soup.select_one(".x-sellercard-atf__info__about-seller a, .x-store-information__store-name, a[href*='/usr/']")
    seller = _t(seller_el) or None
    if seller:
        seller = re.sub(r"^(Visit|Seller:)\s*", "", seller, flags=re.I)[:80]

    loc = re.search(r"Located in:?\s*([^.]{3,80}?)(?=\s+Save |\s+Shop |\.|$)", text)
    ret = re.search(r"(\d+)\s*days?\s*returns?", text, re.I)
    ship = re.search(r"(Free (?:international )?(?:\d-\d day )?(?:delivery|shipping))", text, re.I)

    img = soup.select_one(".ux-image-carousel-item img, img[src*='ebayimg']")
    cond_raw = _t(soup.select_one(".x-item-condition-text, [data-testid='x-item-condition']"))
    if not cond_raw:
        m = re.search(r"Condition:\s*(New|Pre-Owned|Open Box|Refurbished|For parts[^ ]*|Brand New)", text, re.I)
        cond_raw = m.group(1) if m else ""
    cond = next((c for c in ("Brand New", "New", "Pre-Owned", "Open Box", "Refurbished", "For parts")
                 if c.lower() in cond_raw.lower()), None) or (cond_raw[:40] or None)

    ids = [i for i in dict.fromkeys(ITEM_RE.findall(html)) if i != item_id]


    return {
        "title": title or None,
        "price_usd": price,
        "currency": "USD" if price is not None else None,
        "condition": cond or None,
        "watchers": int(watch.group(1).replace(",", "")) if watch else None,
        "units_sold": int(sold.group(1).replace(",", "")) if sold else None,
        "units_is_floor": bool(sold and sold.group(2)) if sold else False,
        "quantity_text": qty.group(0) if qty else None,
        "quantity_n": _num(qty.group(0)) if qty else None,
        "seller": seller,
        "seller_feedback_pct": float(pct.group(1)) if pct else None,
        "seller_items_sold": _magnitude(sold_items.group(1), sold_items.group(2)) if sold_items else None,
        "free_shipping": bool(ship and "free" in ship.group(1).lower()),
        "shipping_text": ship.group(1) if ship else None,
        "returns_text": ret.group(0) if ret else None,
        "location": loc.group(1).strip() if loc else None,
        "image_url": (img.get("src") if img else None),
        "related_ids": ids[:30],
    }


def item_id_of(html: str) -> str | None:
    m = re.search(r'"itemId"\s*:\s*"?(\d{9,15})', html) or ITEM_RE.search(html)
    return m.group(1) if m else None
