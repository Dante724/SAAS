#!/usr/bin/env python3
"""Builds the industry landing pages, sitemap.xml, robots.txt and site.webmanifest.

Run from the repo root:  python3 website/tools/build_pages.py
Change DOMAIN below (or run the sed one-liner in website/README.md) once the domain is final.
"""
import html
import json
from datetime import date
from pathlib import Path

DOMAIN = "https://tekly.app"
ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today().isoformat()
e = lambda s: html.escape(s, quote=False)

PAGES = [
    {
        "slug": "phone-repair-software", "noun": "Phone and computer repair",
        "template": "Electronics",
        "title": "Phone & Computer Repair Software | Tekly",
        "desc": "Cell phone, tablet and computer repair shop software. IMEI or serial at intake, AI estimates, customer approvals by phone, parts and POS. From $29/mo.",
        "h1": "Phone and computer repair software",
        "lead": "One electronics template for phones, tablets, laptops and desktops. Tekly asks for the IMEI or the serial number depending on the device, drafts the estimate from the customer's words and gets their approval by phone.",
        "fields": [("Device type", "Phone, tablet, laptop or desktop"), ("Brand and model", "e.g. iPhone 14 Pro"), ("IMEI or serial", "IMEI for phones and tablets, serial for computers"), ("Passcode", "Stored encrypted"), ("Storage", "e.g. 256 GB"), ("Condition photos", "Taken at the counter")],
        "features": [
            ("AI estimates from the complaint", "“Dropped in water, won't charge” becomes priced lines for diagnostics, board cleaning and parts, from your own catalog."),
            ("Approval on the customer's phone", "Found a cracked back glass mid-repair? The customer ticks it and signs. Nothing is added unless they approve."),
            ("Screens and batteries booked to the job", "Order parts from the ticket. When they arrive and you scan them, they're written off to the right repair."),
            ("Sell accessories and refurbs", "Quick POS for cases, chargers and refurbished devices, with AI-written product descriptions."),
            ("Customers who never have to call", "A tracking link shows status, photos and the ready time — and lets them pay online."),
            ("Know what each repair earns", "Parts cost, labour and technician commission post to every job automatically."),
        ],
        "faq": [
            ("Does Tekly record IMEI numbers?", "Yes. The electronics template asks for the IMEI on phones and tablets and the serial number on computers, and keeps it on the device's history."),
            ("Are customer passcodes stored safely?", "Passcodes are encrypted at rest and only visible to staff whose role allows it."),
            ("Can I sell accessories and refurbished devices?", "Yes. The built-in POS handles retail sales alongside repairs, with stock tracked by location and serial number."),
        ],
    },
    {
        "slug": "auto-repair-shop-software", "noun": "Vehicle repair",
        "template": "Vehicles",
        "title": "Auto Repair Shop Software for Cars, Trucks & RVs | Tekly",
        "desc": "Auto repair shop software for cars, trucks, RVs and bikes. VIN intake, AI estimates, digital approvals by text, parts and payroll. Plans from $29/mo.",
        "h1": "Auto repair shop software for cars, trucks, RVs and bikes",
        "lead": "One vehicle template from a family hatchback to a fleet truck or an RV. Check vehicles in by VIN or plate, draft the estimate with AI, and let customers approve each line from their phone.",
        "fields": [("VIN", "Kept on the vehicle's history"), ("Year, make and model", "e.g. 2019 Honda CR-V"), ("Plate", "Find the vehicle by plate"), ("Mileage or hours", "Logged every visit"), ("Unit number", "For fleet vehicles"), ("Condition photos", "Taken at check-in")],
        "features": [
            ("AI estimates from the complaint", "“Grinding when braking, pulls left” becomes an inspection, pads and rotors, and a caliper service — at your prices."),
            ("Digital approvals by text", "Customers approve each line from their phone and sign. Declined work is saved for the next visit."),
            ("Parts booked to the job", "Order from the repair order. Received parts are written off to the right vehicle and costed into the margin."),
            ("Fleet-ready", "Unit numbers and account billing for trucks and fleets, with every service on the vehicle's history."),
            ("Bays that keep moving", "A job board shows what's waiting on parts, approval or a technician, with time in each stage."),
            ("Payroll from closed jobs", "Technician commission and hours post when the job closes, ready for the pay run."),
        ],
        "faq": [
            ("Does Tekly work for truck, fleet and RV repair?", "Yes. The vehicle template covers cars, trucks, RVs and bikes, with unit numbers and account billing for fleets."),
            ("Can customers approve repairs by text?", "Yes. They get a link by SMS or WhatsApp, approve each line and sign on their phone."),
            ("Does it keep each vehicle's service history?", "Every job stays on the vehicle's record, found by VIN or plate."),
        ],
    },
    {
        "slug": "small-engine-repair-software", "noun": "Small engine repair",
        "template": "Tools & small engine",
        "title": "Small Engine & Power Tool Repair Software | Tekly",
        "desc": "Repair shop software for small engines and power tools: mowers, saws, generators and cordless tools. Intake, estimates, approvals, parts and POS.",
        "h1": "Small engine and power tool repair software",
        "lead": "One template for outdoor power equipment and workshop tools. Capture engine hours or voltage at intake, separate warranty from paid work, and keep the seasonal rush moving.",
        "fields": [("Item", "e.g. 21-inch push mower"), ("Brand and model", "Quick-pick common brands"), ("Serial number", "Kept on the item's history"), ("Engine model or voltage", "Engine for mowers, voltage for tools"), ("Hours", "For engines"), ("Warranty status", "Warranty or paid")],
        "features": [
            ("AI estimates in seconds", "“Won't start after winter” becomes a tune-up, carburettor clean and parts, from your catalog."),
            ("Warranty and paid jobs side by side", "Mark each job warranty or paid and keep the paperwork straight."),
            ("Seasonal bookings", "Online booking with real time slots so tune-ups are scheduled before spring."),
            ("Approval on the customer's phone", "Customers approve extra work line by line. Nothing is added unless they choose it."),
            ("Parts that know their job", "Order from the ticket and book received parts to the right repair automatically."),
            ("Sell parts and equipment", "Quick POS for blades, oil, filters and new equipment."),
        ],
        "faq": [
            ("Can I separate warranty work from paid repairs?", "Yes. Each job records its warranty status so warranty and paid work stay separate in reports."),
            ("Can customers book a tune-up online?", "Yes. Online booking shows real available time slots and sends reminders."),
            ("Does it handle both engines and cordless tools?", "Yes. The same template captures engine hours for small engines and voltage for power tools."),
        ],
    },
    {
        "slug": "marine-repair-software", "noun": "Marine repair",
        "template": "Marine",
        "title": "Marine Repair & Boat Service Software | Tekly",
        "desc": "Marine repair software for boatyards and outboard shops. HIN and engine serial intake, AI estimates, customer approvals, parts and payroll.",
        "h1": "Marine repair and boat service software",
        "lead": "A template built for boatyards — from winterising to a full repower. Track the hull ID and each engine separately, draft estimates with AI and get approvals before the boat comes out of the water.",
        "fields": [("HIN", "Hull identification number"), ("Engine serial", "Each engine tracked separately"), ("Make, model and year", "e.g. 19 ft centre console"), ("Length", "In feet"), ("Registration", "State or national"), ("Condition photos", "Taken at haul-out")],
        "features": [
            ("AI estimates from the complaint", "“Won't idle, stalls in gear” becomes a diagnostic, fuel service and parts — at your rates."),
            ("Every engine on its own record", "Twin outboards? Each engine keeps its own serial and service history under the vessel."),
            ("Approval before you start", "Owners approve each line from their phone, wherever they are."),
            ("Parts booked to the job", "Impellers, filters and lower-unit parts are written off to the right boat on arrival."),
            ("Seasonal scheduling", "Winterising and spring commissioning booked online with reminders."),
            ("Payroll from closed jobs", "Technician hours and commission post automatically when the job closes."),
        ],
        "faq": [
            ("Can I track more than one engine per boat?", "Yes. Each engine has its own serial and history under the vessel's hull ID."),
            ("Can owners approve work remotely?", "Yes. They approve each line and sign from a link on their phone."),
            ("Does it handle winterising bookings?", "Yes. Online booking and reminders cover seasonal work like winterising and commissioning."),
        ],
    },
]

ICON = '<path fill-rule="evenodd" d="M59.50 27.67Q62.00 32.00 59.50 36.33L49.50 53.65Q47.00 57.98 42.00 57.98L22.00 57.98Q17.00 57.98 14.50 53.65L4.50 36.33Q2.00 32.00 4.50 27.67L14.50 10.35Q17.00 6.02 22.00 6.02L42.00 6.02Q47.00 6.02 49.50 10.35ZM24.5 19.5L34.5 15V25.5H42V33H34.5V40C34.5 41.8 35.4 42.5 37.2 42.5H42V49.8H35.8C28.6 49.8 24.5 46 24.5 39.5V33H20.5V25.5H24.5Z"/>'

# Fonts are self-hosted (assets/fonts, OFL-1.1): no visitor IP goes to Google.
FONT_PRELOADS = (
    '  <link rel="preload" href="/assets/fonts/sora-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>\n'
    '  <link rel="preload" href="/assets/fonts/inter-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>')

LEGAL_LINKS = '<a href="/privacy/">Privacy</a><a href="/terms/">Terms</a>'


def footer(current=""):
    return f'''<footer class="foot">
  <div class="wrap">
    <div class="foot-grid">
      <div><a href="/" class="logo"><svg aria-hidden="true"><use href="#mark"/></svg>tekly</a><p class="foot-about">Repair shop software, from the first complaint to the final payment.</p></div>
      <div><h2 class="foot-h">Software for</h2><ul>{nav_links(current)}</ul></div>
      <div><h2 class="foot-h">Company</h2><ul><li><a href="/#start">Book a demo</a></li><li><a data-mail href="/#start">Contact</a></li><li><a href="/#faq">FAQ</a></li></ul></div>
      <div><h2 class="foot-h">Account</h2><ul><li><a data-app="signin" href="/#start">Sign in</a></li><li><a data-app="signup" href="/#start">Start free trial</a></li></ul></div>
    </div>
    <div class="legal"><span>© <span id="yr">{date.today().year}</span> Tekly</span><span class="legal-links">{LEGAL_LINKS}</span><span>Product screens show sample data.</span></div>
  </div>
</footer>'''


def nav_links(current):
    items = "".join(
        f'<li><a href="/{p["slug"]}/"{" aria-current=\"page\"" if p["slug"] == current else ""}>{e(p["template"])}</a></li>'
        for p in PAGES)
    return items


def page(p):
    url = f"{DOMAIN}/{p['slug']}/"
    ld = [
        {"@context": "https://schema.org", "@type": "WebPage", "name": p["title"], "url": url, "description": p["desc"],
         "isPartOf": {"@id": DOMAIN + "/#website"}},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Tekly", "item": DOMAIN + "/"},
            {"@type": "ListItem", "position": 2, "name": p["h1"], "item": url}]},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in p["faq"]]},
    ]
    fields = "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in p["fields"])
    feats = "".join(f'<article class="card rv"><h3>{e(t)}</h3><p>{e(d)}</p></article>' for t, d in p["features"])
    faq = "".join(f'<details><summary>{e(q)}<svg aria-hidden="true"><use href="#plus"/></svg></summary><p>{e(a)}</p></details>' for q, a in p["faq"])
    others = "".join(f'<li><a class="link" href="/{o["slug"]}/">{e(o["h1"])}</a></li>' for o in PAGES if o is not p)
    main = f'''
<section class="hero sub-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Tekly</a><span aria-hidden="true">/</span><span>{e(p["template"])} template</span></nav>
    <h1 class="h-display">{e(p["h1"])}</h1>
    <p class="hero-sub">{e(p["lead"])}</p>
    <div class="hero-ctas"><a class="btn btn-brand btn-lg" data-app="signup" data-cta="hero_trial" href="/#start">Start free trial</a><a class="btn btn-ghost btn-lg" href="/#start" data-cta="hero_demo">Book a demo</a></div>
    <p class="mono hero-fine"><span data-trial>14</span>-day trial · no card · from $29 a month</p>
  </div>
</section>

<section class="sec">
  <div class="wrap two-col">
    <div class="rv">
      <p class="eyebrow">At intake</p>
      <h2 class="h-section">The right details, the moment the job comes in.</h2>
      <p class="lede">The {e(p["template"])} template sets up the fields your counter needs, so nothing is missing when the job reaches the bench.</p>
    </div>
    <div class="tpl-form rv" aria-label="Intake fields"><div class="tpl-form-h"><span>New job · {e(p["template"])} intake</span></div><dl class="sub-dl">{fields}</dl></div>
  </div>
</section>

<section class="sec sec-cream">
  <div class="wrap">
    <div class="sec-head"><p class="eyebrow rv">What you get</p><h2 class="h-section rv">Everything from the counter to the payout.</h2></div>
    <div class="ai-grid">{feats}</div>
  </div>
</section>

<section class="sec sec-tight">
  <div class="wrap"><div class="sub-price rv">
    <div><p class="eyebrow">Pricing</p><h2 class="h-sub">Plans from $29 a month.</h2><p class="lede">Solo is $29 for up to 2 people. Shop is $69 for up to 8 ($49 per extra location) and adds inventory, scanning, the time clock and reports. Pro is $129 for the first location ($79 each extra) with no limits, plus purchase orders, payroll, AI estimate drafting and accounting sync. Every trial includes all Pro features for 14 days, no card.</p></div>
    <div class="sub-price-ctas"><a class="btn btn-brand btn-lg" data-app="signup" data-cta="pricing_trial" href="/#start">Start free trial</a><a class="link" href="/#pricing">Compare plans</a></div>
  </div></div>
</section>

<section class="sec sec-tight">
  <div class="wrap faq">
    <div><p class="eyebrow">Questions</p><h2 class="h-sub">{e(p["noun"])}, answered.</h2><p class="lede">More questions? <a class="link" href="/#faq">Read the full FAQ</a></p></div>
    <div>{faq}</div>
  </div>
</section>

<section class="sec sec-tight sec-cream">
  <div class="wrap"><p class="eyebrow">Other templates</p><ul class="others">{others}</ul></div>
</section>
'''
    return shell(p, url, main, ld)


def shell(p, url, main, ld):
    """Head, header and footer shared by every generated page; `main` is the page body."""
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{e(p["title"])}</title>
  <meta name="description" content="{html.escape(p["desc"])}">
  <link rel="canonical" href="{url}">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <meta name="theme-color" content="#003dff">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Tekly">
  <meta property="og:url" content="{url}">
  <meta property="og:title" content="{html.escape(p["title"])}">
  <meta property="og:description" content="{html.escape(p["desc"])}">
  <meta property="og:image" content="{DOMAIN}/assets/og.png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:image" content="{DOMAIN}/assets/og.png">
  <link rel="icon" type="image/svg+xml" href="/assets/tekly-mark.svg">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/icon-32.png">
  <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
  <link rel="manifest" href="/site.webmanifest">
{FONT_PRELOADS}
  <link rel="stylesheet" href="/styles.css">
  <script>document.documentElement.classList.add("js")</script>
  <script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
</head>
<body class="sub">
<a class="skip" href="#main">Skip to content</a>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="mark" viewBox="0 0 64 64">{ICON}</symbol>
  <symbol id="plus" viewBox="0 0 16 16"><path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></symbol>
</svg>
<header class="nav" id="nav">
  <div class="wrap nav-in">
    <a href="/" class="logo" aria-label="Tekly home"><svg aria-hidden="true"><use href="#mark"/></svg>tekly</a>
    <nav class="nav-links" aria-label="Primary"><a href="/#product">Product</a><a href="/#industries">Industries</a><a href="/#ai">AI</a><a href="/#pricing">Pricing</a><a href="/#faq">FAQ</a></nav>
    <div class="nav-right">
      <a class="nav-signin" data-app="signin" data-cta="nav_signin" href="/#start">Sign in</a>
      <a class="btn btn-quiet" href="/#start" data-cta="nav_demo">Book a demo</a>
      <a class="btn btn-brand nav-cta" data-app="signup" data-cta="nav_trial" href="/#start">Start free trial</a>
      <button class="burger" aria-label="Menu" aria-expanded="false" aria-controls="mmenu"><span></span><span></span></button>
    </div>
  </div>
  <div class="mmenu" id="mmenu"><a href="/#product">Product</a><a href="/#industries">Industries</a><a href="/#ai">AI</a><a href="/#pricing">Pricing</a><a href="/#faq">FAQ</a>
    <div class="mmenu-ctas"><a class="btn btn-quiet" data-app="signin" href="/#start">Sign in</a><a class="btn btn-brand" data-app="signup" href="/#start">Start free trial</a></div></div>
</header>

<main id="main">{main}</main>

{footer(p["slug"])}
<script defer src="/site.config.js"></script>
<script defer src="/page.js"></script>
<script defer src="/marketing.js"></script>
<script defer src="/assistant.js"></script>
</body>
</html>
'''


def legal_page(doc):
    """Privacy / Terms: the landing pages' head, header and footer around a plain text body."""
    url = f"{DOMAIN}/{doc['slug']}/"
    body = (ROOT / "tools" / "legal" / f"{doc['slug']}.html").read_text(encoding="utf-8")
    ld = [{"@context": "https://schema.org", "@type": "WebPage", "name": doc["title"], "url": url,
           "description": doc["desc"], "isPartOf": {"@id": DOMAIN + "/#website"}}]
    main = (f'<section class="sec"><div class="wrap doc"><p class="eyebrow">Legal</p>'
            f'<h1 class="h-section">{e(doc["h1"])}</h1><p class="mono doc-date">Last updated {e(doc["updated"])}</p>'
            f'{body}</div></section>')
    return shell(doc, url, main, ld)

LEGAL = [
    {"slug": "privacy", "title": "Privacy Policy | Tekly", "h1": "Privacy policy", "updated": "19 October 2026",
     "desc": "What Tekly collects on this website and in the app, why, who processes it for us, and your rights."},
    {"slug": "terms", "title": "Terms of Service | Tekly", "h1": "Terms of service", "updated": "19 October 2026",
     "desc": "The terms for using Tekly: accounts, trials, billing, cancellation and refunds, your data, and acceptable use."},
]


def main():
    for p in PAGES:
        d = ROOT / p["slug"]
        d.mkdir(exist_ok=True)
        (d / "index.html").write_text(page(p), encoding="utf-8")
    placeholders = []
    for doc in LEGAL:
        d = ROOT / doc["slug"]
        d.mkdir(exist_ok=True)
        out = legal_page(doc)
        (d / "index.html").write_text(out, encoding="utf-8")
        if "[[" in out:
            placeholders.append(doc["slug"])
    if placeholders:
        print("WARNING: fill in the [[…]] placeholders in tools/legal/ before launch:", ", ".join(placeholders))
    urls = ([("/", "1.0")] + [(f"/{p['slug']}/", "0.8") for p in PAGES]
            + [(f"/{d['slug']}/", "0.3") for d in LEGAL])
    (ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{DOMAIN}{u}</loc><lastmod>{TODAY}</lastmod><priority>{pr}</priority></url>\n" for u, pr in urls)
        + "</urlset>\n", encoding="utf-8")
    (ROOT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: /tools/\n\nSitemap: {DOMAIN}/sitemap.xml\n", encoding="utf-8")
    (ROOT / "site.webmanifest").write_text(json.dumps({
        "name": "Tekly", "short_name": "Tekly", "start_url": "/", "display": "standalone",
        "background_color": "#ffffff", "theme_color": "#003dff",
        "icons": [{"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/assets/icon-512.png", "sizes": "512x512", "type": "image/png"}]}, indent=2), encoding="utf-8")
    print("built", len(PAGES), "pages + sitemap.xml, robots.txt, site.webmanifest")


if __name__ == "__main__":
    main()
