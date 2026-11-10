"""Build the owner-facing audit page (design-audit/report/index.html + img/) from council/plan.json."""
import json, os, sys, subprocess, html, re

A = sys.argv[1]                      # design-audit folder
P = json.load(open(os.path.join(A, "council/plan.json")))
COV = json.load(open(os.path.join(A, "data/coverage.json")))
SHIFT_ROWS = len(json.load(open(os.path.join(A, "data/tab_shift.json"))))
R = os.path.join(A, "report"); IMG = os.path.join(R, "img"); os.makedirs(IMG, exist_ok=True)
e = lambda s: html.escape(str(s if s is not None else ""))

used = []
def short(n): return "%03d.jpg" % (used.index(n) + 1)
def img(name, alt="", cls="shot"):
    if not name: return ""
    src = os.path.join(A, "screens", name)
    if not os.path.exists(src): return f'<div class="missing">missing: {e(name)}</div>'
    if name not in used:
        used.append(name)
        dst = os.path.join(IMG, short(name))
        if not os.path.exists(dst):
            w = 1000 if "__d-" in name else 390
            subprocess.run(["sips", "-Z", str(w), "-s", "formatOptions", "70", src, "--out", dst], capture_output=True)
    phone = "__m-" in name
    cap = re.sub(r"\.jpg$", "", name).replace("__", " · ")
    return f'<figure class="{cls}{" phone" if phone else ""}"><button type="button" class="zoom" data-src="img/{short(name)}" aria-label="Enlarge screenshot"><img loading="lazy" src="img/{short(name)}" alt="{e(alt or cap)}"></button><figcaption>{e(cap)}</figcaption></figure>'

SEV = {"P0": ("Must fix", "sev-p0"), "P1": ("Should fix", "sev-p1"), "P2": ("Polish", "sev-p2")}
def sev(s): t, c = SEV.get(s, (s, "")); return f'<span class="pill {c}">{e(s)} · {t}</span>'
EFF = {"S": "Small (≤ 2 h)", "M": "Medium (≤ 1 day)", "L": "Large (> 1 day)"}
NAMES = {}
for c in COV: NAMES[c["page"]] = c["url"]
def screen_chips(lst):
    if not isinstance(lst, list): return f'<span class="chip">{e(lst)}</span>'
    return "".join(f'<span class="chip">{e(s)}</span>' for s in lst)

s = P["summary"]; cov = P["coverage"]
groups = P["groups"]; bugs = P["bugs"]; ts = P["tabShift"]; stable = P.get("stable", []); rules = P["rules"]; phases = P["phases"]; dec = P["decisions"]

# ---------- sections ----------
out = []
sev_counts = s["bySeverity"]; bug_counts = s["bugsBySeverity"]
out.append(f'''
<header class="top">
  <div class="brand"><svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><path d="M16 2.5l11.7 6.75v13.5L16 29.5 4.3 22.75V9.25z" fill="var(--accent)"/><circle cx="16" cy="16" r="5.2" fill="var(--bg)"/></svg><span>Tekly · UI design audit</span></div>
  <h1>Why the app feels inconsistent, and the plan to fix it</h1>
  <p class="lede">{e(s["headline"])}</p>
  <p class="status"><span class="dot"></span><span>Audit and plan only, no code changed. <strong>Waiting for your approval.</strong></span></p>
</header>
<nav class="toc" aria-label="Sections"><a href="#summary">Summary</a><a href="#findings">Findings</a><a href="#bugs">Bugs</a><a href="#tab-shift">Tab shifts</a><a href="#rules">Rules</a><a href="#plan">Plan</a><a href="#decide">Decide</a><a href="#coverage">Coverage</a></nav>
<section id="summary">
  <h2>Summary</h2>
  <div class="tiles">
    <div class="tile"><b>{s["issueGroups"]}</b><span>root causes</span><small><i class="sev-p0">{sev_counts.get("P0",0)} must fix</i> · <i class="sev-p1">{sev_counts.get("P1",0)} should</i> · <i class="sev-p2">{sev_counts.get("P2",0)} polish</i></small></div>
    <div class="tile"><b>{s["bugs"]}</b><span>bugs found on the way</span><small>{bug_counts.get("P0",0)} P0 · {bug_counts.get("P1",0)} P1 · {bug_counts.get("P2",0)} P2 · {len(s.get("bugsFlaggedToBackend",[]))} for backend</small></div>
    <div class="tile"><b>{s["tabShiftRows"]}</b><span>tab switches that move things</span><small>{s.get("stableTabBars",0)} tab bars already stable</small></div>
    <div class="tile"><b>{len(phases)}</b><span>phases, quick wins first</span><small>{e(s["effortTotal"])}</small></div>
  </div>
  <p class="note">Checked: <b>{cov["screens"]}</b> screens · <b>{cov["pageTabs"]}</b> page tabs · <b>{cov["drawersAndPanels"]}</b> drawers and panels · <b>{cov["screenshots"]:,}</b> screenshots at desktop 1440 and phone 390, light and dark, every scroll step · <b>{SHIFT_ROWS}</b> tab-switch measurements. Raw findings: designers {s["rawFindings"]["designersTotal"]}, engineers {s["rawFindings"]["eng1LayoutCauses"] + s["rawFindings"]["eng2ComponentCauses"]} root causes, merged into the {s["issueGroups"]} groups below.</p>
  <div class="good"><h3>Already right</h3><ul>{"".join(f"<li>{e(x)}</li>" for x in s.get("whatsRightAlready", []))}</ul></div>
</section>''')

# findings
fx = []
for g in groups:
    scr = g["screens"]; n = len(scr) if isinstance(scr, list) else None
    fx.append(f'''<article class="card" id="{e(g["id"].lower())}">
  <div class="card-h">{sev(g["severity"])}<span class="pill eff">{e(EFF.get(g["effort"], g["effort"]))}</span><span class="rid">{e(g["id"])}</span></div>
  <h3>{e(g["title"])}</h3>
  <p>{e(g["symptom"])}</p>
  <div class="shots">{"".join(img(x) for x in g["screenshots"][:2])}</div>
  <p class="fix"><b>Fix once:</b> {e(g["fix"])}</p>
  <details><summary>Screens affected{f" ({n})" if n else ""} · cause in code</summary>
    <div class="chips">{screen_chips(scr)}</div>
    <p class="mono">{e(g["cause"])}</p>
    <p class="muted">Merges {e(", ".join(g.get("mergedIds", [])))}</p>
  </details>
</article>''')
out.append(f'<section id="findings"><h2>Findings by root cause</h2><p class="sub">Each card is one shared cause. Fixing it once removes every symptom listed under it. Tap a screenshot to enlarge it.</p>{"".join(fx)}</section>')

# bugs
bx = []
for b in sorted(bugs, key=lambda b: b["severity"]):
    flag = f'<span class="pill flag">{e(b["flag"])}</span>' if b.get("flag") else f'<span class="pill eff">Phase {e(b.get("phase"))}</span>'
    bx.append(f'''<details class="bug"><summary>{sev(b["severity"])} <span class="bt">{e(b["title"])}</span> {flag}</summary>
  <p>{e(b["detail"])}</p><p class="mono">{e(b["cause"])}</p><div class="shots">{"".join(img(x) for x in b["screenshots"][:2])}</div></details>''')
out.append(f'<section id="bugs"><h2>Bugs found along the way</h2><p class="sub">Real defects spotted while auditing. The ones marked for the backend team touch data and stay out of this plan.</p>{"".join(bx)}</section>')

# tab shift
def dd(v):
    if not v: return '<span class="zero">0</span>'
    dx, dy = v.get("dx", 0) or 0, v.get("dy", 0) or 0
    if abs(dx) < 0.5 and abs(dy) < 0.5: return '<span class="zero">0</span>'
    parts = []
    if abs(dx) >= 0.5: parts.append(f'{"→" if dx > 0 else "←"} {abs(dx):g}')
    if abs(dy) >= 0.5: parts.append(f'{"↓" if dy > 0 else "↑"} {abs(dy):g}')
    extra = f' · width {v["dw"]:+g}' if v.get("dw") else ""
    return '<span class="mv">' + " ".join(parts) + " px</span>" + e(extra)
rows = []
for t in ts:
    shots = "".join(img(x) for x in (t.get("desktopShots") or [])[:2]) + "".join(img(x) for x in (t.get("phoneShots") or [])[:2])
    rows.append(f'''<tr><td><b>{e(t["screen"])}</b><br><span class="muted">{e(t["tabBar"])} → {e(t["tabSwitchedTo"])}</span></td>
<td>{e(t["whatMoves"])}{(" <span class=muted>" + e(t["note"]) + "</span>") if t.get("note") else ""}
<details class="pair"><summary>Before / after</summary><div class="shots four">{shots}</div></details></td>
<td class="num">{dd(t.get("desktop"))}</td><td class="num">{dd(t.get("phone"))}</td><td class="num"><a href="#{e(t["group"].lower())}">{e(t["group"])}</a><br><span class="muted">Phase {e(t["fixedInPhase"])}</span></td></tr>''')
st = "".join(f'<li><b>{e(x["screen"])}</b> · {e(x["tabBar"])}{(" <span class=muted>(" + e(x["note"]) + ")</span>") if x.get("note") else ""}</li>' for x in stable)
fp = P.get("falsePositivesDropped") or []
fpx = "".join(f'<li>{e(x if isinstance(x, str) else (x.get("what") or x.get("item") or x.get("description") or json.dumps(x)))}</li>' for x in fp)
out.append(f'''<section id="tab-shift"><h2>Tab-shift table</h2>
<p class="sub">Measured with the page header, action buttons, tab bar and first content line taken before and after every tab switch. → / ↓ is how far it moved, in pixels. Target after the plan: every row reads 0.</p>
<div class="tablewrap"><table class="shift"><thead><tr><th>Screen · tab</th><th>What moves</th><th class="num">Desktop 1440</th><th class="num">Phone 390</th><th class="num">Fix</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<details class="more"><summary>{len(stable)} tab bars that already stay still</summary><ul class="cols">{st}</ul></details>
{f'<details class="more"><summary>{len(fp)} measurements discarded as false alarms</summary><ul>{fpx}</ul></details>' if fp else ""}
</section>''')

# rules
rx = "".join(f'<li><p class="rule">{e(r["rule"])}</p><p class="muted">How we keep it: {e(r["enforce"])}</p></li>' for r in rules)
out.append(f'<section id="rules"><h2>Consistency rules</h2><p class="sub">The house rules every screen will follow. Each one is checked by a shared component, a code check, or the screenshot run.</p><ol class="rules">{rx}</ol></section>')

# phases
px = []
for ph in phases:
    items = "".join(f'<li><span class="pill eff sm">{e(it.get("effort",""))}</span> {e(it["item"])}</li>' for it in ph["items"])
    px.append(f'''<article class="phase"><div class="ph-h"><span class="ph-n">{e(ph["id"])}</span><h3>{e(ph["name"])}</h3><span class="pill eff">{e(ph["effortTotal"])}</span></div>
<p class="sees"><b>What you'll see:</b> {e(ph["ownerSees"])}</p>
<details><summary>{len(ph["items"])} items · risk · how we check it</summary><ul class="items">{items}</ul><p><b>Risk:</b> {e(ph["risk"])}</p><p><b>Verified by:</b> {e(ph["verify"])}</p></details></article>''')
out.append(f'<section id="plan"><h2>Phased fix plan</h2><p class="sub">Quick wins first. Each phase ships as separate pull requests into the beta branch and is re-checked with the same screenshot run.</p>{"".join(px)}</section>')

# decisions
dx = "".join(f'''<li><p class="q">{e(d["question"])}</p><ul class="opts">{"".join(f"<li>{e(o)}</li>" for o in d["options"])}</ul><p class="muted"><b>Recommended:</b> {e(d["recommendation"])}. {e(d.get("why",""))}</p></li>''' for d in dec)
out.append(f'''<section id="decide"><h2>Your decisions, then approval</h2><p class="sub">Five choices that shape the shared pieces. Reply with your picks (or "go with the recommendations") and which phases to start.</p><ol class="decide">{dx}</ol>
<div class="approve"><b>To approve:</b> reply in the Tekly session, e.g. <q>Approved: phases 0 and 1, recommendations on all five.</q> Nothing will be built until you do.</div></section>''')

# coverage
cr = []
for c in sorted(COV, key=lambda c: c["page"]):
    cr.append(f'<tr><td>{e(c["page"])}</td><td class="mono">{e(c["url"])}</td><td class="num">{len(c["cfgs"])}/4</td><td class="num">{len(c["tabs"])}</td><td class="num">{len(c["drawers"])}</td><td class="num">{c["max_scroll_steps"]}</td><td class="num">{c["shots"]}</td></tr>')
out.append(f'''<section id="coverage"><h2>Coverage</h2>
<p class="sub">Every route in the app, Tekly Go, the public pages and the admin panel. Each one was captured at desktop and phone, light and dark, at the top and at every scroll step, plus every tab, the drawer behind the first row, and the main "New…" form.</p>
<details class="more"><summary>All {len(COV)} screens checked</summary><div class="tablewrap"><table class="cov"><thead><tr><th>Screen</th><th>Address</th><th class="num">Views</th><th class="num">Tabs</th><th class="num">Drawers</th><th class="num">Scroll steps</th><th class="num">Shots</th></tr></thead><tbody>{"".join(cr)}</tbody></table></div></details>
<details class="more"><summary>How this was done</summary><ul>
<li>A fresh copy of the beta branch (roadmap/orderry-parity, at the merge of PR #94) ran on this Mac with the demo shop plus a till sale, a check-in point and a customer portal link. Your demo at :8090 was not touched.</li>
<li>Headless Chrome visited every screen in four views. Scrollbars were left visible, so the Windows-style scrollbar shift shows up too.</li>
<li>Council: three senior designers, each owning a third of the screens (design critique and accessibility review); two front-end engineers who traced each cause to its line of code; a design PM who merged the findings into the plan. Design-system and anti-pattern ("AI look") checks were applied by hand. A draft of the updated design tokens is in the repo folder design-audit/council.</li>
<li>Known limits: the capture can't see hover-only states; the admin panel was viewed with a local test admin; data-related bugs are flagged for the backend team only.</li>
</ul></details></section>''')

body = "\n".join(out)
page = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tekly Design Audit</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{ /* Tekly's own palette: canvas, ink, sapphire. Calm single column, cards by role, Stripe-light. */
  --bg:#f7f8fd; --card:#ffffff; --ink:#14213f; --ink2:#435577; --ink3:#5b6886; --line:#e3e7f0; --accent:#0059ba;
  --p0:#b42318; --p0bg:#fdecea; --p1:#9a5b00; --p1bg:#fff4e0; --p2:#3d5a80; --p2bg:#eaf0f8; --ok:#1a7f4b; --okbg:#e8f6ee;
  --display:"Geist", -apple-system, "Segoe UI", sans-serif; --body:"Geist", -apple-system, "Segoe UI", sans-serif; --mono:"Geist Mono", ui-monospace, Menlo, monospace;
  color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#0e1119; --card:#161b26; --ink:#e4e8f2; --ink2:#aab4c9; --ink3:#97a2ba; --line:#2a3142; --accent:#7aa7ff;
  --p0:#ff9b8f; --p0bg:#3a1a19; --p1:#f5c26b; --p1bg:#372a12; --p2:#a9c1e6; --p2bg:#1d2840; --ok:#7fd6a4; --okbg:#14301f; color-scheme: dark; }} }}
:root[data-theme="dark"] {{ --bg:#0e1119; --card:#161b26; --ink:#e4e8f2; --ink2:#aab4c9; --ink3:#97a2ba; --line:#2a3142; --accent:#7aa7ff;
  --p0:#ff9b8f; --p0bg:#3a1a19; --p1:#f5c26b; --p1bg:#372a12; --p2:#a9c1e6; --p2bg:#1d2840; --ok:#7fd6a4; --okbg:#14301f; color-scheme: dark; }}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 var(--body); -webkit-text-size-adjust:100%; }}
main {{ max-width:880px; margin:0 auto; padding:24px 16px 80px; }}
h1,h2,h3 {{ font-family:var(--display); text-wrap:balance; letter-spacing:-.01em; }}
h1 {{ font-size:clamp(24px,5vw,32px); line-height:1.15; margin:10px 0 8px; }}
h2 {{ font-size:22px; margin:44px 0 6px; padding-top:8px; }}
h3 {{ font-size:17px; margin:8px 0 6px; }}
p {{ margin:6px 0; }}
.brand {{ display:flex; align-items:center; gap:8px; font-weight:600; color:var(--ink2); font-size:14px; }}
.lede {{ font-size:17px; color:var(--ink2); max-width:65ch; }}
.status {{ display:flex; gap:8px; align-items:baseline; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:8px 12px; font-size:14px; margin-top:6px; }}
.dot {{ width:8px; height:8px; border-radius:50%; background:var(--p1); flex:none; }}
.toc {{ position:sticky; top:0; z-index:5; display:flex; gap:4px; overflow-x:auto; background:color-mix(in srgb, var(--bg) 92%, transparent); backdrop-filter:saturate(1.2); padding:10px 0; margin:18px -16px 0; padding-left:16px; padding-right:16px; border-bottom:1px solid var(--line); scrollbar-width:none; }}
.toc a {{ flex:none; color:var(--ink2); text-decoration:none; font-size:14px; font-weight:500; padding:8px 12px; border-radius:10px; min-height:40px; display:inline-flex; align-items:center; }}
.toc a:hover, .toc a:focus-visible {{ background:var(--card); color:var(--accent); outline:none; }}
.sub {{ color:var(--ink2); max-width:65ch; }}
.muted {{ color:var(--ink3); font-size:13.5px; }}
.mono {{ font-family:var(--mono); font-size:12.5px; color:var(--ink2); overflow-wrap:anywhere; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:14px 0; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; display:flex; flex-direction:column; gap:2px; }}
.tile b {{ font-size:30px; font-variant-numeric:tabular-nums; line-height:1.1; }}
.tile span {{ font-weight:600; }}
.tile small {{ color:var(--ink3); font-size:13px; }}
.tile i {{ font-style:normal; font-weight:600; }}
i.sev-p0 {{ color:var(--p0); }} i.sev-p1 {{ color:var(--p1); }} i.sev-p2 {{ color:var(--p2); }}
.note {{ font-size:14px; color:var(--ink2); }}
.good {{ background:var(--okbg); border-radius:14px; padding:12px 16px; margin-top:14px; }}
.good h3 {{ margin:0 0 4px; font-size:15px; color:var(--ok); }}
.good ul {{ margin:0; padding-left:18px; }}
.card, .phase {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; margin:14px 0; }}
.card-h, .ph-h {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
.rid {{ margin-left:auto; font-family:var(--mono); font-size:12px; color:var(--ink3); }}
.pill {{ display:inline-flex; align-items:center; border-radius:999px; padding:2px 10px; font-size:12.5px; font-weight:600; white-space:nowrap; }}
.pill.sm {{ font-size:11.5px; padding:1px 8px; }}
.sev-p0 {{ background:var(--p0bg); color:var(--p0); }} .sev-p1 {{ background:var(--p1bg); color:var(--p1); }} .sev-p2 {{ background:var(--p2bg); color:var(--p2); }}
.eff {{ background:var(--bg); color:var(--ink2); border:1px solid var(--line); }}
.flag {{ background:var(--p2bg); color:var(--p2); }}
.fix {{ border-left:3px solid var(--accent); padding-left:10px; margin:10px 0; }}
.shots {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; margin:10px 0; align-items:start; }}
.shots.four {{ grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); }}
figure.shot {{ margin:0; }}
figure.shot.phone {{ max-width:240px; }}
.zoom {{ all:unset; cursor:zoom-in; display:block; border:1px solid var(--line); border-radius:10px; overflow:hidden; background:var(--bg); }}
.zoom:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.zoom img {{ display:block; width:100%; height:auto; }}
figcaption {{ font-family:var(--mono); font-size:11px; color:var(--ink3); margin-top:4px; overflow-wrap:anywhere; }}
details > summary {{ cursor:pointer; color:var(--accent); font-weight:500; font-size:14px; min-height:36px; display:flex; align-items:center; gap:6px; flex-wrap:wrap; }}
details.bug {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:6px 14px; margin:8px 0; }}
details.bug > summary {{ color:var(--ink); }}
.bt {{ font-weight:600; flex:1 1 200px; }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }}
.chip {{ font-family:var(--mono); font-size:12px; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:2px 8px; }}
.tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--card); }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ text-align:left; vertical-align:top; padding:10px 12px; border-bottom:1px solid var(--line); }}
th {{ font-size:12.5px; color:var(--ink3); font-weight:600; background:var(--bg); position:sticky; top:0; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
table.shift td:first-child {{ min-width:140px; }} table.shift td:nth-child(2) {{ min-width:220px; }}
.mv {{ color:var(--p0); font-weight:600; }} .zero {{ color:var(--ok); font-weight:600; }}
td a {{ color:var(--accent); font-weight:600; text-decoration:none; }}
details.pair > summary {{ font-size:13px; min-height:32px; }}
.cols {{ columns:2 260px; padding-left:18px; }}
ol.rules, ol.decide {{ padding-left:22px; }}
ol.rules > li, ol.decide > li {{ margin:12px 0; }}
.rule, .q {{ font-weight:600; }}
.opts {{ margin:4px 0; padding-left:18px; }}
.ph-n {{ font-family:var(--mono); font-size:12px; background:var(--accent); color:var(--card); border-radius:6px; padding:2px 8px; }}
.ph-h h3 {{ margin:0; flex:1 1 220px; }}
.sees {{ margin-top:10px; }}
ul.items {{ padding-left:0; list-style:none; }} ul.items li {{ margin:6px 0; }}
.approve {{ background:var(--card); border:2px solid var(--accent); border-radius:14px; padding:14px 16px; margin-top:16px; }}
.missing {{ color:var(--p0); font-size:12px; }}
.lightbox {{ position:fixed; inset:0; background:rgba(8,12,20,.9); display:none; align-items:center; justify-content:center; z-index:50; padding:16px; cursor:zoom-out; }}
.lightbox.open {{ display:flex; }} .lightbox img {{ max-width:100%; max-height:100%; border-radius:8px; }}
.lightbox button {{ position:absolute; top:12px; right:12px; min-width:44px; min-height:44px; border-radius:22px; border:0; background:#fff; color:#14213f; font:600 15px var(--body); }}
@media (max-width:520px) {{ td, th {{ padding:8px; }} .tile b {{ font-size:26px; }} }}
@media (prefers-reduced-motion: reduce) {{ * {{ scroll-behavior:auto !important; }} }}
html {{ scroll-behavior:smooth; }}
</style></head>
<body><main>
{body}
<p class="muted" style="margin-top:40px">Built from design-audit/council/plan.json · capture data in design-audit/ on branch claude/frontend-design-audit-f26812 (not pushed).</p>
</main>
<div class="lightbox" id="lb" role="dialog" aria-modal="true" aria-label="Screenshot"><button type="button" id="lbx">Close</button><img alt="" id="lbi"></div>
<script>
(() => {{ const lb = document.getElementById('lb'), im = document.getElementById('lbi');
  document.addEventListener('click', (ev) => {{ const z = ev.target.closest('.zoom'); if (z) {{ im.src = z.dataset.src; im.alt = z.querySelector('img').alt; lb.classList.add('open'); document.getElementById('lbx').focus(); }} else if (ev.target.closest('#lb')) lb.classList.remove('open'); }});
  document.addEventListener('keydown', (ev) => {{ if (ev.key === 'Escape') lb.classList.remove('open'); }});
}})();
</script></body></html>'''
open(os.path.join(R, "index.html"), "w").write(page)
print("images", len(used), "html bytes", len(page))
json.dump(used, open(os.path.join(R, "images.json"), "w"))
