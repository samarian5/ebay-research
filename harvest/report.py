"""Static dashboard for op_scores → docs/index.html (GitHub Pages, no build step)."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from . import store

CSS = """
:root{--bg:#0b0d10;--card:#14181d;--line:#242b33;--fg:#e8edf2;--mut:#8b97a5;--accent:#4ade80;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:26px 28px 10px;border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:20px;letter-spacing:-.2px}
.sub{color:var(--mut);font-size:13px}
.kpis{display:flex;gap:26px;padding:16px 28px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
main{padding:20px 28px 60px;max-width:1400px}
.bar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
button{background:var(--card);color:var(--fg);border:1px solid var(--line);padding:6px 12px;border-radius:7px;cursor:pointer;font-size:12px}
button.on{border-color:var(--accent);color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:26px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:13px}
.card h3{margin:0 0 6px;font-size:13px;line-height:1.35;font-weight:600}
.row{display:flex;gap:10px;flex-wrap:wrap;color:var(--mut);font-size:12px;margin:4px 0}
.pill{font-size:10.5px;padding:2px 7px;border-radius:20px;border:1px solid var(--line)}
.copy{color:var(--accent);border-color:var(--accent)}.watch{color:var(--warn);border-color:var(--warn)}
.pass,.unmeasured{color:var(--bad);border-color:var(--bad)}
.why{color:var(--mut);font-size:11.5px;margin-top:6px}
a{color:#93c5fd;text-decoration:none}a:hover{text-decoration:underline}
.empty{color:var(--mut);padding:30px 0}
"""


def money(v) -> str:
    return "—" if v in (None, "") else f"${float(v):,.0f}"


def card(r: dict) -> str:
    v = r["verdict"]
    why = "; ".join(r.get("reasons") or [])
    links = [
        ("listing", r.get("url") or ""),
        ("sold comps", f"https://www.ebay.com/sch/i.html?_nkw={q(r.get('title'))}&LH_Sold=1&LH_Complete=1"),
        ("AliExpress", f"https://www.aliexpress.com/w/wholesale-{q(r.get('title'))}.html"),
        ("Google Shopping", f"https://www.google.com/search?tbm=shop&q={q(r.get('title'))}"),
    ]
    return f"""
    <div class="card" data-verdict="{v}" data-niche="{html.escape(r.get('niche') or '')}" data-watch="{r.get('watchers') or 0}">
      <div class="row"><span class="pill {v}">{v}</span>
        <span class="pill">{html.escape(r.get('action') or '')}</span>
        <span>score <b>{r.get('score')}</b></span></div>
      <h3>{html.escape((r.get('title') or '')[:120])}</h3>
      <div class="row">
        <span><b>{money(r.get('price_usd'))}</b></span>
        <span>{r.get('watchers') if r.get('watchers') is not None else '—'} watchers</span>
        <span>{r.get('units_sold') if r.get('units_sold') is not None else '—'} sold</span>
        <span>{r.get('qty') if r.get('qty') is not None else '—'} avail</span>
      </div>
      <div class="row">
        <span>fee {money(r.get('_fee'))}</span>
        <span>profit/unit <b>{money(r.get('profit_per_unit'))}</b></span>
        <span>{'est ' + money(r.get('est_profit_month')) + '/mo' if r.get('est_profit_month') else 'velocity unknown'}</span>
      </div>
      <div class="why">{html.escape(why)}</div>
      <div class="row" style="margin-top:8px">{"".join(f'<a href="{u}" target="_blank" rel="noopener">{n}</a>' for n, u in links if u)}</div>
    </div>"""


def q(s: str | None) -> str:
    from urllib.parse import quote_plus
    return quote_plus((s or "")[:90])


def build(out_path: str = "docs/opportunities.html") -> dict:
    rows = store.select("op_scores?select=*&order=score.desc&limit=1000")
    items = {i["item_id"]: i for i in store.select("op_items?select=*&state=eq.enriched&limit=5000")}
    niches = store.select("op_niches?select=*&order=keyword")
    merged = []
    for r in rows:
        it = items.get(r["item_id"])
        if not it:
            continue
        price = it.get("price_usd") or 0
        merged.append({**r, **{k: it.get(k) for k in ("title", "price_usd", "watchers", "units_sold",
                                                      "niche", "url", "seller", "location", "quantity_n")},
                       "qty": it.get("quantity_n"),
                       "_fee": round(price * 0.1325 + 0.30, 2) if price else None})
    merged.sort(key=lambda r: ((r.get("est_profit_month") or 0), r.get("score") or 0), reverse=True)

    by_verdict = {v: sum(1 for r in merged if r["verdict"] == v) for v in ("copy", "watch", "pass", "unmeasured")}
    niche_rows = []
    for n in niches:
        group = [r for r in merged if r.get("niche") == n["keyword"]]
        best = max(group, key=lambda r: r.get("score") or 0) if group else None
        niche_rows.append((n["keyword"], len(group),
                           sum(1 for r in group if r["verdict"] == "copy"),
                           (best or {}).get("score")))

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>eBay opportunities — Carson rubric</title><style>{CSS}</style></head><body>
<header><h1>eBay opportunity board</h1>
<div class="sub">Ranked with Carson Commerce's rubric (watchers ≥ 50 · 50–100 sold on one listing · ASP ≥ $30)
· generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {len(merged)} enriched listings</div></header>
<div class="kpis">
  <div class="kpi"><b>{by_verdict['copy']}</b><span>copy candidates</span></div>
  <div class="kpi"><b>{by_verdict['watch']}</b><span>watch</span></div>
  <div class="kpi"><b>{len(merged)}</b><span>scored</span></div>
  <div class="kpi"><b>{len(niches)}</b><span>niches</span></div>
  <div class="kpi"><b>{sum(1 for r in merged if r.get('est_profit_month'))}</b><span>with measured velocity</span></div>
</div>
<main>
<div class="bar">
  <button class="on" data-f="all">All</button>
  <button data-f="copy">Copy</button>
  <button data-f="watch">Watch</button>
  <button data-f="pass">Pass</button>
  <input id="minw" placeholder="min watchers" style="background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:7px;padding:6px 10px;width:120px">
</div>
<h2 style="font-size:14px;color:var(--mut);font-weight:500;margin:0 0 8px">Niches</h2>
<table><thead><tr><th>niche</th><th>scored</th><th>copy</th><th>best score</th></tr></thead><tbody>
{''.join(f"<tr><td>{html.escape(k)}</td><td>{c}</td><td>{cc}</td><td>{bs or '—'}</td></tr>" for k, c, cc, bs in niche_rows)}
</tbody></table>
<h2 style="font-size:14px;color:var(--mut);font-weight:500;margin:0 0 10px">Opportunities</h2>
<div class="grid" id="grid">{''.join(card(r) for r in merged[:200])}</div>
<div class="empty" id="empty" style="display:none">Nothing matches that filter.</div>
</main>
<script>
const cards=[...document.querySelectorAll('.card')];
function apply(f,w){{let n=0;cards.forEach(c=>{{
  const ok=(f==='all'||c.dataset.verdict===f)&&(+(c.dataset.watch||0)>=w);
  c.style.display=ok?'':'none'; if(ok)n++;}});
  document.getElementById('empty').style.display=n?'none':'block';}}
let f='all',w=0;
document.querySelectorAll('.bar button').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('.bar button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); f=b.dataset.f; apply(f,w);}});
document.getElementById('minw').oninput=e=>{{w=+e.target.value||0;apply(f,w);}};
</script></body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return {"cards": len(merged), "copy": by_verdict["copy"], "out": out_path}
