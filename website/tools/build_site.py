"""Builds the main marketing pages from website/src/:

    /            home        hero, every trade, product tour teasers, what's coming, pricing teaser
    /product/    product     the full tour: job flow, approvals, inventory, money, Tekly Go, AI, Sense,
                             security and every feature
    /pricing/    pricing     plans (from site.config.js), two-location example, promises, FAQ
    /about/      about       why we build Tekly, what we believe
    /changelog/  changelog   what's coming soon and what shipped

Every page shares assets/site.css, assets/site.js, one header, one footer, the fullscreen menu
and the demo modal (src/partials/ and src/sections/cta.html). Edit those files, then run:

    python3 website/tools/build_site.py

The industry, comparison, guide and legal pages are built by tools/build_pages.py.
"""
import hashlib
import html
import json
import subprocess
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DOMAIN = "https://tekly.app"
e = html.escape


def read(rel):
    return (SRC / rel).read_text(encoding="utf-8")


def section(name):
    return read(f"sections/{name}.html").rstrip() + "\n\n"


LD = json.loads(read("jsonld.json"))
ORG, WEBSITE, APP, FAQ = (next(b for b in LD if b["@type"] == t) for t in ("Organization", "WebSite", "SoftwareApplication", "FAQPage"))


HOME_FAQ = {**FAQ, "mainEntity": [q for q in FAQ["mainEntity"] if q["name"] in json.loads(read("home-faq.json"))]}
assert len(HOME_FAQ["mainEntity"]) == 5


def crumbs_ld(name, path):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Tekly", "item": DOMAIN + "/"},
        {"@type": "ListItem", "position": 2, "name": name, "item": DOMAIN + path}]}


def phero(crumb, h1, lede, extra="", crumbs_html=None):
    """The navy title card at the top of every page except home."""
    crumbs_html = crumbs_html or f'<a href="/">Tekly</a><span aria-hidden="true">/</span><span aria-current="page">{crumb}</span>'
    return f'''<!-- ════════ PAGE TITLE ════════ -->
<section class="phero" id="top" aria-labelledby="pageTitle">
  <div class="phero-body">
    <nav class="crumbs" aria-label="Breadcrumb">{crumbs_html}</nav>
    <h1 id="pageTitle">{h1}</h1>
    <p class="phero-lede">{lede}</p>
    {extra}
  </div>
</section>

'''


SUBNAV = '''<nav class="subnav" aria-label="On this page" data-subnav>
  <a href="#flow">How a job moves</a><a href="#approvals">Approvals</a><a href="#inventory">Inventory</a><a href="#money">Money</a><a href="#go">Tekly Go app</a><a href="#ai">AI &amp; Sense</a><a href="#security">Security</a><a href="#features">All features</a>
</nav>

'''


def pricing_body():
    """The pricing section without its own title (the page title card carries it)."""
    s = section("pricing")
    s = re.sub(r'\s*<p class="eyebrow">Pricing</p>\s*<h2 class="h2" id="priceTitle"[^>]*>.*?</h2>\s*<p class="lede"[^>]*>.*?</p>', "", s, count=1, flags=re.S)
    s = s.replace('aria-labelledby="priceTitle"', 'aria-label="Plans"').replace('id="pricing"', 'id="plans-sec"')
    return s.replace('<div class="plans" id="plans"></div>', f'<div class="plans" id="plans">{static_plans()}</div>')


def site_config():
    """site.config.js, read with node so the plans in the HTML match the ones site.js draws."""
    js = "global.window={};require(process.argv[1]);process.stdout.write(JSON.stringify(window.TEKLY_SITE))"
    return json.loads(subprocess.run(["node", "-e", js, str(ROOT / "site.config.js")], check=True, capture_output=True, text=True).stdout)


def static_plans():
    """The monthly plan cards, written into the page so crawlers that don't run JavaScript still see prices.
    site.js redraws them (and switches to yearly) in the browser."""
    c = site_config()
    cur, app, path = c.get("currency", "$"), c.get("appUrl", "").rstrip("/"), c.get("signUpPath", "/signup")
    out = []
    for p in c["plans"]:
        extra = (f'<p class="extra">+ {cur}{p["extraLocation"]} for each extra location</p>' if p.get("extraLocation") else "") \
            + (f'<p class="extra">+ {cur}{p["extraSeat"]} for each extra team member</p>' if p.get("extraSeat") else "")
        feats = "".join(f'<li><svg aria-hidden="true"><use href="#check"/></svg>{f}</li>' for f in p["features"])
        out.append(f'''<article class="plan{" is-featured" if p.get("featured") else ""}">
        <div class="plan-top"><h3>{p["name"]}</h3>{f'<span class="badge">{p["badge"]}</span>' if p.get("badge") else ""}</div>
        <p class="tagl">{p["tagline"]}</p>
        <div class="price"><span class="c">{cur}</span><span class="a">{p["monthly"]}</span></div>
        <p class="unit">{p["unit"]}</p>
        {extra}
        <p class="seats">{p["seats"]}</p>
        <a class="btn btn-wide {"btn-solid" if p.get("featured") else "btn-outline"}" href="{app}{path}?plan={p["id"]}&amp;billing=month">{p["cta"]} <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a>
        <ul>{feats}</ul>
      </article>''')
    return "".join(out)


def compare_band():
    return '''<!-- ════════ COMPARE BAND ════════ -->
<section class="card-sec tint compare-band" aria-labelledby="cmpTitle">
  <div>
    <p class="eyebrow">Comparing tools?</p>
    <h2 class="h3" id="cmpTitle" data-reveal>See Tekly side by side with the software you use today.</h2>
  </div>
  <ul class="cmp-links">
    <li><a href="/compare/tekly-vs-repairdesk/" data-spring="nudge">Tekly vs RepairDesk <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a></li>
    <li><a href="/compare/tekly-vs-shopmonkey/" data-spring="nudge">Tekly vs Shopmonkey <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a></li>
    <li><a href="/compare/tekly-vs-repairshopr/" data-spring="nudge">Tekly vs RepairShopr <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a></li>
    <li><a href="/compare/tekly-vs-tekmetric/" data-spring="nudge">Tekly vs Tekmetric <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a></li>
    <li><a href="/compare/tekly-vs-orderry/" data-spring="nudge">Tekly vs Orderry <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a></li>
  </ul>
</section>

'''


def home_stage():
    """The job-flow section as home's one navy 'stage': the screen stays put while the five
    steps scroll past (and a click on a step shows it too)."""
    s = section("flow")
    s = s.replace('<!-- ════════ HOW A JOB MOVES (numbered rows) ════════ -->', '<!-- ════════ HOME: THE NAVY STAGE (job flow; full tour on /product/) ════════ -->')
    s = s.replace('<section class="card-sec" id="flow" aria-labelledby="flowTitle">', '<section class="card-sec navy stage airy" id="flow" aria-labelledby="flowTitle">')
    s = s.replace("</section>", '''  <div class="tour-cta" data-rise><a class="btn btn-light" href="/product/" data-spring="nudge">Take the full product tour <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a><a class="link" href="/product/#features">See every feature</a></div>
</section>''', 1)
    return s


def changelog_body():
    soon = read("sections/changelog-soon.cards.html")
    shipped = read("sections/changelog-oct.cards.html").replace('<article class="new-card">', '<article class="new-card" data-rise data-spring="lift">')
    n_soon, n_shipped = soon.count("<article"), shipped.count("<article")
    return f'''<!-- ════════ CHANGELOG ════════ -->
<section class="card-sec" id="soon" aria-labelledby="m-soon">
  <div class="month"><h2 class="h3" id="m-soon">Coming soon</h2><span>{n_soon} updates in final testing</span></div>
  <div class="new-grid">
{soon}  </div>
</section>

<section class="card-sec tint" id="oct-2026" aria-labelledby="m-2026-10">
  <div class="month"><h2 class="h3" id="m-2026-10">October 2026</h2><span>{n_shipped} updates</span></div>
  <div class="new-grid">
{shipped}  </div>
</section>

'''


PAGES = [
    {
        "path": "/", "out": "index.html", "nav": "/",
        "title": "Repair Shop Software for Every Trade & Location | Tekly",
        "desc": "Repair shop software for electronics, auto, small engine and marine shops. Estimates, approvals on the customer's phone, parts and POS. From $29/mo.",
        "og_title": "Tekly — Repair shop software. Diagnose, approve, get paid.",
        "og_desc": "Estimates, line-by-line approval on the customer's phone, parts and POS for electronics, auto, small engine and marine shops. Every trade and location in one account. From $29 a month.",
        "hero_preload": True,
        "ld": [ORG, WEBSITE, APP, HOME_FAQ],
        "body": lambda: section("hero") + section("intro") + home_stage() + "".join(section(n) for n in ["industries", "home-switch", "home-pricing", "home-faq"]),
    },
    {
        "path": "/product/", "out": "product/index.html", "nav": "/product/",
        "title": "Repair Shop Software Features: Approvals, Parts & POS | Tekly",
        "desc": "Every Tekly feature: IMEI, VIN or HIN intake, line-by-line approvals on the customer's phone, parts booked to the job, POS, payroll and AI.",
        "ld": [{"@context": "https://schema.org", "@type": "WebPage", "name": "Tekly features", "url": DOMAIN + "/product/", "about": {"@id": DOMAIN + "/#software"}},
               crumbs_ld("Product", "/product/")],
        "body": lambda: phero("Product", "Everything a repair shop runs on, in one place.",
                              "From check-in to payout: approvals on the customer's phone, parts that know their job, payroll from closed jobs, and a phone app for every technician.")
        + SUBNAV + "".join(section(n) for n in ["flow", "feats", "go", "stats", "ai", "sense", "switch", "everything"]),
    },
    {
        "path": "/pricing/", "out": "pricing/index.html", "nav": "/pricing/",
        "title": "Repair Shop Software Pricing: Plans from $29 a Month | Tekly",
        "desc": "Tekly pricing: Solo $29, Shop $69 and Pro $129 a month. Add locations from $49. 14-day free trial of every Pro feature, no card, no contract.",
        "ld": [APP, FAQ, crumbs_ld("Pricing", "/pricing/")],
        "body": lambda: phero("Pricing", "Repair shop software pricing, from $29 a month.",
                              'Try everything free for <span data-trial>14</span> days, no card. Then pick the plan that fits — free onboarding and data migration, no setup fee, no contract.')
        + pricing_body() + compare_band() + section("faq"),
    },
    {
        "path": "/about/", "out": "about/index.html", "nav": "/about/",
        "title": "About Tekly | Repair Shop Software Built for the Bench",
        "desc": "Why we build Tekly: repair shop software that earns the trust customers give a repair shop. No contracts, prices on the page, your data always yours.",
        "ld": [{"@context": "https://schema.org", "@type": "AboutPage", "name": "About Tekly", "url": DOMAIN + "/about/", "about": {"@id": DOMAIN + "/#org"}},
               crumbs_ld("About", "/about/")],
        "body": lambda: phero("About", "We build Tekly for the people at the bench.",
                              "A small team making software for repair shops: prices on the page, no contracts, and your data always yours.")
        + section("about"),
    },
    {
        "path": "/changelog/", "out": "changelog/index.html", "nav": "/changelog/",
        "title": "What's New in Tekly | Repair Shop Software Updates",
        "desc": "What's new and coming soon in Tekly: missed calls become inquiries, a total profit report, IMEI and barcode scanning, plus October's phone-app updates.",
        "ld": [{"@context": "https://schema.org", "@type": "WebPage", "name": "What's New in Tekly", "url": DOMAIN + "/changelog/"},
               crumbs_ld("What's new", "/changelog/")],
        "body": lambda: phero("What's new", "What's new in Tekly.",
                              "We ship in small, careful steps and say exactly what's changing. “Coming soon” features are built and in final testing; product screens on this site show sample data.")
        + changelog_body(),
    },
]


HERO_PRELOAD = '''  <link rel="preload" as="image" type="image/webp" href="/assets/photos/bench-hero-1600.webp" imagesrcset="/assets/photos/bench-hero-960.webp 960w, /assets/photos/bench-hero-1600.webp 1600w, /assets/photos/bench-hero-2400.webp 2400w" imagesizes="100vw" fetchpriority="high">
'''


def ver(rel):
    """A short content hash, so browsers fetch a changed file instead of a cached one."""
    return hashlib.sha1((ROOT / rel).read_bytes()).hexdigest()[:8]


def head(p):
    url = DOMAIN + p["path"]
    og_title, og_desc = p.get("og_title", p["title"]), p.get("og_desc", p["desc"])
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{e(p["title"])}</title>
  <meta name="description" content="{e(p["desc"])}">
  <link rel="canonical" href="{url}">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <meta name="theme-color" content="#071633">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Tekly">
  <meta property="og:url" content="{url}">
  <meta property="og:title" content="{e(og_title)}">
  <meta property="og:description" content="{e(og_desc)}">
  <meta property="og:image" content="{DOMAIN}/assets/og.jpg">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="Tekly — repair shop software for every trade: electronics, vehicles, small engine and marine. From $29 a month.">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{e(og_title)}">
  <meta name="twitter:description" content="{e(og_desc)}">
  <meta name="twitter:image" content="{DOMAIN}/assets/og.jpg">
  <link rel="icon" type="image/svg+xml" href="/assets/tekly-mark.svg">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/icon-32.png">
  <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
  <link rel="manifest" href="/site.webmanifest">
  <link rel="preload" href="/assets/fonts/geist-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>
{HERO_PRELOAD if p.get("hero_preload") else ""}  <link rel="modulepreload" href="/assets/site.js?v={ver("assets/site.js")}">
  <link rel="stylesheet" href="/assets/site.css?v={ver("assets/site.css")}">
{p.get("css", "")}  <script>document.documentElement.classList.add("js")</script>
  <script type="application/ld+json">
{json.dumps(p["ld"] if len(p["ld"]) > 1 else p["ld"][0], indent=1, ensure_ascii=False)}
  </script>
</head>
<body>
'''


def mark_current(markup, nav):
    """aria-current on the header/menu links that point at this page."""
    return re.sub(rf'(<a (?:class="[^"]*" )?href="{re.escape(nav)}")(?=[ >])', r'\1 aria-current="page"', markup) if nav else markup


def render(p, body=None):
    """A whole page: shared head, header, floating bar, footer, menu and demo form around `body`."""
    hdr = mark_current(read("partials/hdr.html"), p.get("nav", ""))
    body = body if body is not None else p["body"]()
    out = (head(p).replace("<body>", '<body class="has-subnav">' if "data-subnav" in body else "<body>")
           + read("partials/sprite.html") + "\n" + hdr + "\n<main id=\"main\">\n\n"
           + body + section("cta") + "</main>\n\n" + read("partials/modal.html")
           + '''<script defer src="/site.config.js"></script>
<script defer src="/marketing.js"></script>
<script type="module" src="/assets/site.js?v=''' + ver("assets/site.js") + '''"></script>
</body>
</html>
''')
    assert "{{" not in out, p["path"]
    return out


def build(p):
    out = render(p)
    dest = ROOT / p["out"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")
    return len(out)


if __name__ == "__main__":
    for p in PAGES:
        print(f'{p["path"]:<12} {build(p):>7} bytes')
