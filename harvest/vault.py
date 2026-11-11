"""Write the scan into the Obsidian vault, in the vault's existing note style."""
from __future__ import annotations

import os
from datetime import datetime

from . import store

VAULT = os.path.expanduser("~/Documents/Obsidian/Ebay/Opportunity Finder")
METHOD_LINK = "[[Method - The Carson Rubric]]"


def _money(v) -> str:
    return "—" if v in (None, "") else f"${float(v):,.0f}"


def write(date: str | None = None) -> str:
    date = date or datetime.now().strftime("%Y-%m-%d")
    rows = store.select("op_scores?select=*&order=score.desc&limit=500")
    items = {i["item_id"]: i for i in store.select("op_items?select=*&limit=5000")}
    niches = store.select("op_niches?select=*&order=keyword")
    merged = [{**r, **{k: (items.get(r["item_id"]) or {}).get(k) for k in
                       ("title", "price_usd", "watchers", "units_sold", "niche", "url", "seller", "quantity_n")}}
              for r in rows if r["item_id"] in items]
    merged.sort(key=lambda r: ((r.get("est_profit_month") or 0), r.get("score") or 0), reverse=True)

    copy = [r for r in merged if r["verdict"] == "copy"]
    watch = [r for r in merged if r["verdict"] == "watch"]

    lines = [
        "---",
        f"date: {date}",
        "tags: [ebay, opportunity-scan]",
        f"scanned: {len(merged)}",
        f"copy: {len(copy)}",
        "method: carson-rubric",
        "---",
        "",
        f"# Opportunity scan — {date}",
        "",
        f"Rubric: {METHOD_LINK} · watchers ≥ 50 · 50–100 sold on one listing · ASP ≥ $30 · avoid list applied.",
        "",
        "## Scorecard",
        "",
        "| Niche | Scored | Copy | Watch | Best score | Median price |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for n in niches:
        group = [r for r in merged if r.get("niche") == n["keyword"]]
        if not group:
            continue
        prices = sorted(float(r["price_usd"]) for r in group if r.get("price_usd"))
        med = prices[len(prices) // 2] if prices else None
        best = max((r.get("score") or 0) for r in group)
        lines.append(f"| {n['keyword']} | {len(group)} | "
                     f"{sum(1 for r in group if r['verdict'] == 'copy')} | "
                     f"{sum(1 for r in group if r['verdict'] == 'watch')} | {best:.1f} | {_money(med)} |")

    lines += ["", "## Copy candidates", ""]
    if not copy:
        lines.append("_None cleared all three gates today. That is a result, not a failure — "
                     "see the watch list for what is close._")
    for r in copy[:12]:
        price = float(r.get("price_usd") or 0)
        fee = round(price * 0.1325 + 0.30, 2)
        lines += [
            f"### {r['title'][:110]}",
            "",
            f"- **Price** {_money(price)} · **watchers** {r.get('watchers') or '—'} · "
            f"**sold** {r.get('units_sold') or '—'} · **avail** {r.get('quantity_n') or '—'}",
            f"- **Fee** {_money(fee)} → **profit/unit** {_money(r.get('profit_per_unit'))} "
            f"(at {int(0.35 * 100)}% landed cost, an assumption)",
            f"- **Action** {r.get('action')} · score {r.get('score')}",
            f"- Why: {'; '.join(r.get('reasons') or [])}",
            f"- [Listing]({r.get('url')}) · "
            f"[Sold comps](https://www.ebay.com/sch/i.html?_nkw={_q(r.get('title'))}&LH_Sold=1&LH_Complete=1) · "
            f"[AliExpress](https://www.aliexpress.com/w/wholesale-{_q(r.get('title'))}.html)",
            "",
        ]

    lines += ["## Watch list", "", "| Watchers | Sold | Price | Listing |", "|---:|---:|---:|---|"]
    for r in watch[:20]:
        lines.append(f"| {r.get('watchers') or '—'} | {r.get('units_sold') or '—'} | "
                     f"{_money(r.get('price_usd'))} | [{r['title'][:60]}]({r.get('url')}) |")

    lines += ["", "## Caveats", "",
              "- Watchers are only what eBay printed; a missing count is blank, never zero.",
              "- Units on a search card ending in `+` are a floor; item-page units are exact.",
              "- Monthly profit needs two captures of the same listing. Until then it stays blank.",
              "- Landed cost is an assumption (35% of price) until a real source price is measured.", ""]

    os.makedirs(f"{VAULT}/Scans", exist_ok=True)
    path = f"{VAULT}/Scans/{date} - Scan.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _q(s: str | None) -> str:
    from urllib.parse import quote_plus
    return quote_plus((s or "")[:90])
