"""Before/after tab-shift numbers for the plan's tab-shift rows: python3 compare.py <before dir> <after dir> [phase]"""
import json, sys, os, collections

B, A = sys.argv[1], sys.argv[2]
phase = sys.argv[3] if len(sys.argv) > 3 else None
plan = json.load(open(os.path.join(B, "council/plan.json")))

def load(d):
    rows = json.load(open(os.path.join(d, "data/tab_shift.json")))
    out = collections.defaultdict(list)
    for r in rows:
        if r["where"] in ("tab",): out[(r["page"], r["tab"].strip().lower(), r["cfg"])].append(r)
    return out
before, after = load(B), load(A)

def move(r):
    """What the owner sees move: header, tab bar, tab buttons, toolbar controls (not the content itself)."""
    if not r: return None
    vals = [abs(m["dx"]) for m in r["controls_moved"]] + [abs(m["dy"]) for m in r["controls_moved"]] + [r["tab_buttons_max_dx"]]
    for k in ("title", "tabbar"):
        if isinstance(r[k], list): vals += [abs(r[k][0]), abs(r[k][1])]
    fc = r["first_content"]
    if isinstance(fc, list): vals += [abs(fc[0]), abs(fc[1])]
    return round(max(vals + [0]), 1)

def pick(m, page, tab, cfg):
    t = tab.strip().lower()
    for (p, tb, c), rs in m.items():
        if p == page and c == cfg and (tb == t or tb.startswith(t[:10]) or t.startswith(tb[:10])): return rs[0]
    return None

lines = ["| Screen | Tab | Fixed in | Desktop before → after | Phone before → after |", "|---|---|---|---|---|"]
zero = tot = 0
for row in plan["tabShift"]:
    ph = str(row.get("fixedInPhase"))
    if phase is not None and ph != phase: continue
    cells = []
    for cfg in ("d-light", "m-light"):
        b, a = pick(before, row["screen"], row["tabSwitchedTo"], cfg), pick(after, row["screen"], row["tabSwitchedTo"], cfg)
        mb, ma = move(b), move(a)
        cells.append(f"{'–' if mb is None else mb} → {'–' if ma is None else ma}")
        if mb and ma is not None:
            tot += 1; zero += (ma < 1)
    lines.append(f"| {row['screen']} | {row['tabSwitchedTo']} | Phase {ph} | {cells[0]} px | {cells[1]} px |")
print("\n".join(lines))
print(f"\nMeasured pairs that moved before: {tot}; now under 1px: {zero}")
