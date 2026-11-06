"""Turn design-audit/records.jsonl into tables the council and the report use."""
import json, os, sys, collections, re

W = sys.argv[1]
recs = [json.loads(l) for l in open(os.path.join(W, "records.jsonl"))]
out = os.path.join(W, "data"); os.makedirs(out, exist_ok=True)

def d(a, b):
    if not a or not b: return None
    return (round(b["x"] - a["x"], 1), round(b["y"] - a["y"], 1), round(b["w"] - a["w"], 1))

# ---------- tab shift ----------
shift = []
seen_shift = set()
for r in recs:
    if "after" not in r: continue
    f, a = r["first"], r["after"]
    row = {"page": r["page"], "cfg": r["cfg"], "where": r["state"], "group": r.get("group"), "tab": r["tab"], "i": r["tabIndex"],
           "shot": (r.get("files") or [None])[0]}
    row["title"] = d(f.get("title"), a.get("title"))
    row["heading"] = d(f.get("h1"), a.get("h1")) if f.get("h1Text") == a.get("h1Text") else ("changed" if f.get("h1") or a.get("h1") else None)
    row["tabbar"] = d(f.get("tabbar"), a.get("tabbar"))
    fa = {}
    for x in f["actions"]: fa.setdefault(x["t"], x)
    moves = []
    for x in a["actions"]:
        y = fa.get(x["t"])
        if y and (abs(x["x"] - y["x"]) > 0.5 or abs(x["y"] - y["y"]) > 0.5): moves.append({"t": x["t"], "dx": round(x["x"] - y["x"], 1), "dy": round(x["y"] - y["y"], 1)})
    row["controls_moved"] = moves
    row["controls_appear"] = [x["t"] for x in a["actions"] if x["t"] not in fa][:6]
    row["controls_vanish"] = [t for t in fa if t not in {x["t"] for x in a["actions"]}][:6]
    tabs_d = [d(p, q) for p, q in zip(f.get("tabs", []), a.get("tabs", []))]
    row["tab_buttons_max_dx"] = max([abs(t[0]) for t in tabs_d if t] + [0])
    row["tab_active_dw"] = max([abs(t[2]) for t in tabs_d if t] + [0])
    fc, ac = f.get("firstContent"), a.get("firstContent")
    row["first_content"] = d(fc, ac)
    vh = 900 if r["cfg"].startswith("d-") else 844
    fb, ab = f.get("tabbar"), a.get("tabbar")
    row["autoscrolled"] = bool(fb and ab and (fb["y"] < 0 or fb["y"] + fb["h"] > vh - 10) and abs(ab["y"] - fb["y"]) > 1)
    if row["autoscrolled"]:
        dy = ab["y"] - fb["y"]
        rel = lambda t: t and (t[0], round(t[1] - dy, 1), t[2])
        row["first_content"] = rel(row["first_content"]); row["tabbar"] = (row["tabbar"][0], 0.0, row["tabbar"][2])
        moves = [dict(m, dy=round(m["dy"] - dy, 1)) for m in moves]
        moves = [m for m in moves if abs(m["dx"]) > 0.5 or abs(m["dy"]) > 0.5]
        row["controls_moved"] = moves
        if isinstance(row["heading"], tuple): row["heading"] = rel(row["heading"])
    row["scroll_w"] = (f["scW"], a["scW"]) if f["scW"] != a["scW"] else None
    mags = [abs(v) for k in ("title", "tabbar", "first_content") for v in ((row[k] or (0, 0))[:2] if isinstance(row[k], tuple) else (0, 0))]
    mags += [abs(m["dx"]) for m in moves] + [abs(m["dy"]) for m in moves] + [row["tab_buttons_max_dx"]]
    if isinstance(row["heading"], tuple): mags += [abs(row["heading"][0]), abs(row["heading"][1])]
    row["max_px"] = max(mags + [0])
    k = (row["page"], row["cfg"], row["where"], row["tab"])
    if k in seen_shift: continue
    seen_shift.add(k); shift.append(row)
json.dump(shift, open(os.path.join(out, "tab_shift.json"), "w"), indent=1)

# ---------- coverage ----------
cov = collections.defaultdict(lambda: {"cfgs": set(), "tabs": set(), "drawers": set(), "shots": 0, "errors": [], "scroll_steps": 0, "finalUrl": None})
for r in recs:
    c = cov[r["page"]]; c["url"] = r["url"]
    c["shots"] += len(r.get("files") or [])
    if r["state"] == "default": c["cfgs"].add(r["cfg"]); c["scroll_steps"] = max(c["scroll_steps"], len(r.get("files") or [])); c["finalUrl"] = r.get("finalUrl")
    if r["state"] == "tab": c["tabs"].add(r["tab"])
    if r["state"] in ("row", "action"): c["drawers"].add(r["state"] + ": " + (r.get("label") or "")[:40])
    if r["state"] == "error": c["errors"].append(r["cfg"] + " " + r["error"][:80])
covl = [{"page": k, "url": v.get("url"), "finalUrl": v["finalUrl"], "cfgs": sorted(v["cfgs"]), "tabs": sorted(v["tabs"]), "drawers": sorted(v["drawers"]), "shots": v["shots"], "max_scroll_steps": v["scroll_steps"], "errors": v["errors"]} for k, v in cov.items()]
json.dump(covl, open(os.path.join(out, "coverage.json"), "w"), indent=1)

# ---------- off-design metrics ----------
btn_variants = collections.Counter(); btn_examples = {}
small = collections.defaultdict(list); contrast = collections.defaultdict(list); trunc = collections.defaultdict(list)
extax = collections.defaultdict(set); overflow = {}; empties = []; radii = collections.Counter(); icons = collections.Counter(); strokes = collections.Counter()
titles = []; contentLeft = collections.defaultdict(set); fonts = collections.Counter(); tables = []
for r in recs:
    m = r.get("metrics")
    if not m: continue
    key = f'{r["page"]}|{r["state"]}|{r.get("tab") or r.get("label") or ""}'
    for b in m["buttons"]:
        if not b["t"] and b["w"] < 50: kind = "icon"
        else: kind = "text"
        v = (kind, b["h"], b["rad"], b["fs"], b["fw"])
        btn_variants[v] += 1; btn_examples.setdefault(v, (r["page"], r["cfg"], b["t"], (r.get("files") or [None])[0]))
    if r["cfg"].startswith("m-"):
        for s in m["small"]: small[r["page"]].append(s)
    for c in m["contrast"]: contrast[(r["page"], r["cfg"])].append(c)
    for t in m["trunc"]: trunc[(r["page"], r["cfg"])].append(t)
    for e in m["exTax"]: extax[r["page"]].add(e)
    if m["docOverflow"] > 1: overflow[(r["page"], r["cfg"], r["state"], r.get("tab") or r.get("label") or "")] = m["docOverflow"]
    for e in m["empties"]: empties.append({"page": r["page"], "cfg": r["cfg"], **e})
    for k, v in m["cards"].items(): radii[k] += v
    for k, v in m["iconSizes"].items(): icons[k] += v
    for k, v in m["strokes"].items(): strokes[k] += v
    if r["state"] in ("default", "tab"):
        if m.get("title"): titles.append({"page": r["page"], "cfg": r["cfg"], "state": r["state"], "tab": r.get("tab"), **m["title"]})
        if m.get("h1"): titles.append({"page": r["page"], "cfg": r["cfg"], "state": r["state"], "tab": r.get("tab"), "kind": "heading", **m["h1"]})
        if m.get("contentLeft") is not None: contentLeft[r["cfg"]].add((m["contentLeft"], r["page"] + ("/" + r["tab"] if r.get("tab") else "")))
    fonts[m["fontFam"][:60]] += 1
    for t in m["tables"]:
        if t["mism"]: tables.append({"page": r["page"], "cfg": r["cfg"], "state": r["state"], "tab": r.get("tab"), "mism": t["mism"]})

def top(c, n=40): return [[list(k) if isinstance(k, tuple) else k, v] for k, v in c.most_common(n)]
summary = {
    "records": len(recs), "screens_shot": sum(len(r.get("files") or []) for r in recs),
    "button_variants": [{"v": list(k), "n": n, "eg": btn_examples[k]} for k, n in btn_variants.most_common(60)],
    "button_variant_count": len(btn_variants),
    "touch_small_by_page": {k: {"n": len(v), "eg": v[:8]} for k, v in sorted(small.items(), key=lambda x: -len(x[1]))},
    "contrast": {f"{k[0]}|{k[1]}": v[:10] for k, v in contrast.items() if v},
    "truncation": {f"{k[0]}|{k[1]}": v[:10] for k, v in trunc.items() if v},
    "ex_tax": {k: sorted(v) for k, v in extax.items()},
    "horizontal_overflow": [[*k, v] for k, v in overflow.items()],
    "empty_states": empties,
    "card_radius": top(radii), "icon_sizes": top(icons), "icon_strokes": top(strokes),
    "titles": titles, "content_left": {k: sorted(v) for k, v in contentLeft.items()},
    "fonts": top(fonts), "table_alignment": tables[:80],
}
json.dump(summary, open(os.path.join(out, "metrics_summary.json"), "w"), indent=1, default=list)

# quick console digest
print("records", len(recs), "shots", summary["screens_shot"], "pages", len(covl))
print("button variants", len(btn_variants))
big = sorted([s for s in shift if s["max_px"] >= 2], key=lambda s: -s["max_px"])
print("tab switches", len(shift), "with movement >=2px", len(big))
for s in big[:25]: print(f'  {s["max_px"]:>6} {s["cfg"]} {s["page"]} [{s["tab"]}] title={s["title"]} bar={s["tabbar"]} first={s["first_content"]} moved={[(m["t"][:12], m["dx"], m["dy"]) for m in s["controls_moved"][:3]]} tabdx={s["tab_buttons_max_dx"]}')
print("touch<44 pages", len(small), "contrast pages", len(summary["contrast"]), "trunc", len(summary["truncation"]), "extax", summary["ex_tax"], "overflow", len(overflow))
print("fonts", summary["fonts"][:4])
