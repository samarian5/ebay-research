"""Carson rubric applied to item-level facts → op_scores + dashboard + CSV.

Every constant carries its source (video id @ second), copied from the vault:
  * watchers ≥ 50 is the primary demand signal       d0eTsSZjS-M @ 00:04:27
  * 50–100 units sold on ONE listing is the proof test  d0eTsSZjS-M @ 00:04:27
  * under $30 is the ceiling, $100 is the target        d0eTsSZjS-M @ 00:04:05 / 00:04:56
  * sell-through ≥ 35%                                  h03Gu4yUI5A @ 00:04:09
  * avoid list: apparel, tees, mugs, board games, ink   d0eTsSZjS-M @ 00:05:13

Nothing here is imputed: a missing watcher count stays missing, and a monthly
profit is only computed when two snapshots give a real units/day.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

from . import store

WATCH_STRONG = 50
WATCH_MID = 20
PROOF_UNITS = 50
ASP_CEILING = 30
ASP_TARGET = 100
FEE_PCT, FEE_FIXED = 0.1325, 0.30
SOURCING_RATIO = 0.35          # assumption, labelled in output
AVOID = ("t-shirt", "tshirt", "tee", "shirt", "apparel", "hoodie", "mug", "board game",
         "printer ink", "toner", "damaged", "for parts", "as is")


def fee(price: float) -> float:
    return round(price * FEE_PCT + FEE_FIXED, 2)


def tokens(title: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in (title or "").lower()).split()
            if len(t) > 2}


def velocity(snaps: list[dict]) -> tuple[float | None, float | None, int]:
    """(watchers/day, units/day, snapshots) from the oldest and newest capture."""
    pts = sorted((s for s in snaps if s.get("captured_at")), key=lambda s: s["captured_at"])
    if len(pts) < 2:
        return None, None, len(pts)
    a, b = pts[0], pts[-1]
    t0 = datetime.fromisoformat(a["captured_at"].replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(b["captured_at"].replace("Z", "+00:00"))
    days = (t1 - t0).total_seconds() / 86400
    if days <= 0:
        return None, None, len(pts)
    dw = (b.get("watchers") or 0) - (a.get("watchers") or 0)
    du = (b.get("units_sold") or 0) - (a.get("units_sold") or 0)
    return round(dw / days, 2), round(du / days, 2), len(pts)


def score_item(it: dict, vibe: tuple, rivals: int) -> dict:
    price = float(it.get("price_usd") or 0)
    watch = it.get("watchers")
    units = it.get("units_sold") or 0
    wday, uday, ns = vibe
    title = (it.get("title") or "").lower()

    reasons: list[str] = []
    avoided = next((a for a in AVOID if a in title), None)

    # -- gates ---------------------------------------------------------
    if avoided:
        verdict, action = "pass", "ignore"
        reasons.append(f"on Carson's avoid list ({avoided})")
    elif price and price < 20:
        verdict, action = "pass", "ignore"
        reasons.append(f"${price:.0f} is under his $20 floor — fee eats it")
    elif (watch or 0) >= WATCH_STRONG or units >= PROOF_UNITS:
        verdict = "copy"
        action = "copy now" if price >= 45 else "find source first"
        if (watch or 0) >= WATCH_STRONG:
            reasons.append(f"{watch} watchers ≥ 50")
        if units >= PROOF_UNITS:
            reasons.append(f"{units} sold on one listing ≥ 50")
    elif (watch or 0) >= WATCH_MID or units >= 20:
        verdict, action = "watch", "watch"
        reasons.append(f"{watch or 0} watchers / {units} sold — below his 50 gate")
    else:
        verdict, action = "unmeasured", "ignore"
        reasons.append("no demand signal observed")

    # -- components ----------------------------------------------------
    demand = (0.45 * min(1.0, (watch or 0) / 200)
              + 0.30 * min(1.0, units / 500)
              + 0.15 * min(1.0, max(0.0, uday or 0) / 2)
              + 0.10 * min(1.0, max(0.0, wday or 0) / 5))

    if price >= ASP_TARGET:
        econ = 1.0
    elif price >= 45:
        econ = 0.80
    elif price >= ASP_CEILING:
        econ = 0.55
    elif price >= 20:
        econ = 0.25
    else:
        econ = 0.0

    comp = 1 / (1 + 0.5 * math.log2(rivals + 1))
    weight = {"copy": 1.0, "watch": 0.85, "unmeasured": 0.5, "pass": 0.3}[verdict]
    score = round(100 * (0.55 * demand + 0.30 * econ + 0.15 * min(1.0, units / 200)) * comp * weight, 1)

    # A 35% landed-cost assumption is only meaningful for resellable goods in
    # Carson's band. For antiques/one-offs above $250 it would invent a margin,
    # so the profit figure stays blank and the action says so.
    profit_unit = None
    profit_month = None
    if price and 20 <= price <= 250:
        profit_unit = round(price - fee(price) - price * SOURCING_RATIO, 2)
        if uday and uday > 0:
            profit_month = round(profit_unit * uday * 30, 2)
    elif price > 250:
        reasons.append(f"${price:,.0f} is a one-off/collectible — landed cost cannot be assumed")

    if price and price < ASP_CEILING:
        reasons.append(f"price ${price:.0f} is under his $30 ceiling")

    if price > 250 and action == "copy now":
        action = "find source first"

    return {"item_id": it["item_id"], "verdict": verdict, "action": action, "score": score,
            "demand": round(demand, 3), "economics": round(econ, 3), "competition": round(comp, 3),
            "watchers_per_day": wday, "units_per_day": uday, "profit_per_unit": profit_unit,
            "est_profit_month": profit_month, "reasons": reasons}


def run(limit: int = 2000) -> dict:
    items = store.select(f"op_items?select=*&state=eq.enriched&limit={limit}")
    snaps = store.select("op_snapshots?select=item_id,captured_at,watchers,units_sold&order=captured_at.asc&limit=20000")
    by_item: dict[str, list[dict]] = defaultdict(list)
    for s in snaps:
        by_item[s["item_id"]].append(s)

    # rivals: near-duplicate titles inside the same niche
    by_niche: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_niche[it.get("niche") or "-"].append(it)
    rival_count: dict[str, int] = {}
    for group in by_niche.values():
        toks = {it["item_id"]: tokens(it.get("title")) for it in group}
        for it in group:
            a = toks[it["item_id"]]
            rival_count[it["item_id"]] = sum(
                1 for other in group
                if other["item_id"] != it["item_id"] and len(a & toks[other["item_id"]]) >= 3
            )

    rows = [score_item(it, velocity(by_item.get(it["item_id"], [])), rival_count.get(it["item_id"], 0))
            for it in items]
    for r in rows:
        r["scored_at"] = datetime.now(timezone.utc).isoformat()
        r["reasons"] = r["reasons"]          # jsonb accepts a list
    store.upsert("op_scores", rows, "item_id", keep_nulls=True)  # full overwrite: a null must clear a stale value

    # join back for reporting
    meta = {it["item_id"]: it for it in items}
    out = []
    for r in rows:
        it = meta[r["item_id"]]
        out.append({**r, "title": it.get("title"), "price_usd": it.get("price_usd"),
                    "watchers": it.get("watchers"), "units_sold": it.get("units_sold"),
                    "niche": it.get("niche"), "url": it.get("url"), "seller": it.get("seller"),
                    "location": it.get("location"), "qty": it.get("quantity_n")})
    out.sort(key=lambda r: (r["est_profit_month"] or 0, r["score"]), reverse=True)

    os.makedirs("out", exist_ok=True)
    with open("out/opportunities.csv", "w", encoding="utf-8") as f:
        cols = ["item_id", "verdict", "action", "score", "niche", "title", "price_usd",
                "watchers", "units_sold", "units_per_day", "profit_per_unit", "est_profit_month", "url"]
        f.write(",".join(cols) + "\n")
        for r in out:
            f.write(",".join('"' + str(r.get(c) if r.get(c) is not None else "").replace('"', "'") + '"'
                             for c in cols) + "\n")
    with open("out/scores.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    return {"scored": len(out),
            "copy": sum(1 for r in out if r["verdict"] == "copy"),
            "watch": sum(1 for r in out if r["verdict"] == "watch"),
            "with_profit": sum(1 for r in out if r["est_profit_month"])}
