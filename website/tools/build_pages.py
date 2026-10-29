#!/usr/bin/env python3
"""Builds the industry landing pages, sitemap.xml, robots.txt and site.webmanifest.

Run from the repo root:  python3 website/tools/build_pages.py
Change DOMAIN below (or run the sed one-liner in website/README.md) once the domain is final.
"""
import html
import json
import subprocess
from datetime import date
from pathlib import Path

from compare_pages import CHECKED, COMPETITORS, GUIDE, TEKLY_PRICES
import re
import build_site

DOMAIN = "https://tekly.app"
ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today().isoformat()


def lastmod(url):
    """The day a page last changed: today if its built file differs from git, else its last commit date."""
    rel = (url.strip("/") + "/index.html") if url != "/" else "index.html"
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if run("status", "--porcelain", "--", rel):
        return TODAY
    return run("log", "-1", "--format=%cs", "--", rel) or TODAY
e = lambda s: html.escape(s, quote=False)

PAGES = [
    {
        "slug": "phone-repair-software", "vs": [("repairdesk", "RepairDesk"), ("repairshopr", "RepairShopr"), ("orderry", "Orderry")], "noun": "Phone and computer repair",
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
        "who": ["Cell phone repair stores", "Computer and laptop repair shops", "Tablet and console repair benches", "Refurbishers that sell devices too", "Repair chains with several stores", "Shops with a counter and a separate workshop"],
        "steps": [
            ("Check in", "Pick Phone, Tablet, Laptop or Desktop. Tekly asks for the IMEI or serial, stores the passcode encrypted and takes condition photos at the counter."),
            ("Estimate", "On Pro, Tekly AI turns “dropped in the pool, won't charge” into priced lines from your own catalog. You review and send."),
            ("Approval", "The customer approves each line on their phone and signs. A cracked back glass found mid-repair waits for their tick."),
            ("Parts", "Order the screen from the ticket. Scan the box when it arrives and it's charged to that repair, with the cost in the margin."),
            ("Pickup and pay", "The tracking link says it's ready. They pay at the counter or online, and the technician's commission posts when the job closes."),
        ],
        "photo": ("iphone-mat", "An iPhone opened up for repair on a blue work mat", 'Photo: Unsplash (<a href="https://unsplash.com/license" rel="noopener license">Unsplash License</a>)'),
        "more_faq": [
            ("How much does phone repair software cost?", "Tekly starts at $29 a month for up to 2 people. Shop is $69 a month for 4 people and adds inventory and barcode scanning; Pro is $129 and adds purchase orders, payroll and AI estimate drafting. Every trial includes all Pro features for 14 days with no card."),
            ("Can I move my customers and devices from my current system?", "Yes. Send an export from your current system or spreadsheets and we bring customers, devices and stock across, free on every plan. You can take your first job the day you sign up."),
            ("Do customers get updates without calling the shop?", "Yes. Each repair has a tracking link with status, photos and the ready time, and updates go out by SMS, WhatsApp or email."),
        ],
        "faq": [
            ("Does Tekly record IMEI numbers?", "Yes. The electronics template asks for the IMEI on phones and tablets and the serial number on computers, and keeps it on the device's history."),
            ("Are customer passcodes stored safely?", "Passcodes are encrypted at rest and only visible to staff whose role allows it."),
            ("Can I sell accessories and refurbished devices?", "Yes. The built-in POS handles retail sales alongside repairs, with stock tracked by location and serial number."),
        ],
    },
    {
        "slug": "auto-repair-shop-software", "vs": [("shopmonkey", "Shopmonkey"), ("tekmetric", "Tekmetric"), ("orderry", "Orderry")], "noun": "Vehicle repair",
        "template": "Vehicles",
        "title": "Auto Repair Shop Software for Cars, Trucks & RVs | Tekly",
        "desc": "Auto repair shop software for cars, trucks, RVs and bikes. VIN intake, AI estimates, digital approvals by text, parts and payroll. Plans from $29/mo.",
        "h1": "Auto repair shop software for cars, trucks, RVs and bikes",
        "lead": "Auto repair software with one vehicle template, from a family hatchback to a fleet truck or an RV. Check vehicles in by VIN or plate, draft the estimate with AI, and let customers approve each line from their phone.",
        "fields": [("VIN", "Kept on the vehicle's history"), ("Year, make and model", "e.g. 2019 Honda CR-V"), ("Plate", "Find the vehicle by plate"), ("Mileage or hours", "Logged every visit"), ("Unit number", "For fleet vehicles"), ("Condition photos", "Taken at check-in")],
        "features": [
            ("AI estimates from the complaint", "“Grinding when braking, pulls left” becomes an inspection, pads and rotors, and a caliper service — at your prices."),
            ("Digital approvals by text", "Customers approve each line from their phone and sign. Declined work is saved for the next visit."),
            ("Parts booked to the job", "Order from the repair order. Received parts are written off to the right vehicle and costed into the margin."),
            ("Fleet-ready", "Unit numbers and account billing for trucks and fleets, with every service on the vehicle's history."),
            ("Bays that keep moving", "A job board shows what's waiting on parts, approval or a technician, with time in each stage."),
            ("Payroll from closed jobs", "Technician commission and hours post when the job closes, ready for the pay run."),
        ],
        "who": ["Independent auto repair shops", "Truck and fleet service shops", "RV service centres", "Motorcycle and e-bike workshops", "Tire, brake and quick-service shops", "Multi-bay and multi-location garages"],
        "steps": [
            ("Check in", "Find the vehicle by VIN or plate, log the mileage and take condition photos at the counter. Every visit stays on the vehicle's history."),
            ("Estimate", "On Pro, Tekly AI turns “grinding when braking, pulls left” into an inspection, pads, rotors and labour at your prices. You review and send."),
            ("Approval", "The customer approves each line on their phone and signs. Declined work is saved for the next visit."),
            ("Parts", "Order from the repair order. Received parts are charged to the right vehicle and their cost lands in the job's margin."),
            ("Pickup and pay", "Payment at the counter or by link. Technician commission and hours post when the job closes, ready for the pay run."),
        ],
        "photo": ("mechanic-stool", "A mechanic on a low wooden stool working on the front brake of a raised pickup truck", 'Photo: <a href="https://commons.wikimedia.org/wiki/File:DSC_6745_A_mechanic_sitting_on_a_small_wooden_stool_working_on_the_front_suspension_of_a_raised_pickup_truck_in_a_garage.jpg" rel="noopener">PattayaPatrol</a>, CC BY-SA 4.0 (<a href="https://creativecommons.org/licenses/by-sa/4.0" rel="noopener license">licence</a>)'),
        "more_faq": [
            ("How much does auto repair shop software cost with Tekly?", "Plans start at $29 a month for up to 2 people. Shop is $69 a month for 4 people across your locations and adds inventory, scanning, the time clock and reports; Pro is $129 and adds purchase orders, payroll and commissions, AI estimate drafting and QuickBooks or Xero sync. Prices are published and month to month."),
            ("Can I switch from Shopmonkey or another shop system?", "Yes. Send an export from your current system or spreadsheets and we bring customers, vehicles and stock across, free on every plan."),
            ("Does Tekly work with QuickBooks?", "Yes. Pro syncs invoices and payments with QuickBooks or Xero both ways."),
        ],
        "faq": [
            ("Does Tekly work for truck, fleet and RV repair?", "Yes. The vehicle template covers cars, trucks, RVs and bikes, with unit numbers and account billing for fleets."),
            ("Can customers approve repairs by text?", "Yes. They get a link by SMS or WhatsApp, approve each line and sign on their phone."),
            ("Does it keep each vehicle's service history?", "Every job stays on the vehicle's record, found by VIN or plate."),
        ],
    },
    {
        "slug": "small-engine-repair-software", "vs": [("repairshopr", "RepairShopr"), ("repairdesk", "RepairDesk"), ("orderry", "Orderry")], "noun": "Small engine repair",
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
        "who": ["Lawn mower and outdoor power equipment shops", "Chainsaw and trimmer dealers with a service bench", "Generator repair and service", "Power tool repair centres", "Rental yards that service their own fleet", "Seasonal repair shops"],
        "steps": [
            ("Check in", "Record the item, serial and engine hours for small engines, or voltage for cordless tools, and mark the job warranty or paid."),
            ("Estimate", "On Pro, Tekly AI turns “won't start after winter” into a tune-up, carburettor clean and parts from your catalog. You review and send."),
            ("Approval", "The customer approves extra work line by line on their phone. Nothing is added unless they choose it."),
            ("Parts", "Order from the ticket. Received parts are booked to the right repair automatically, and low stock drafts the reorder."),
            ("Pickup and pay", "The tracking link says it's ready. Sell blades, oil and filters at the same counter with the quick POS."),
        ],
        "photo": ("generator-yard", "A small-engine portable generator in a tubular frame, outdoors on gravel", 'Photo: <a href="https://commons.wikimedia.org/wiki/File:Let%27s_Generate!_(29004208321).jpg" rel="noopener">Alan Levine</a>, CC BY 2.0 (<a href="https://creativecommons.org/licenses/by/2.0" rel="noopener license">licence</a>)'),
        "more_faq": [
            ("How much does small engine repair software cost?", "Tekly starts at $29 a month for up to 2 people. Shop is $69 a month and adds inventory, barcode scanning and reports; Pro is $129 and adds purchase orders, payroll and AI estimate drafting. Every trial includes all Pro features for 14 days, no card."),
            ("Can I track engine hours and serial numbers?", "Yes. The template records the serial and engine hours for small engines, or the voltage for power tools, and keeps them on the item's history."),
            ("Can I move my customer list across?", "Yes. Send an export or spreadsheet and we bring customers, items and stock across, free on every plan."),
        ],
        "faq": [
            ("Can I separate warranty work from paid repairs?", "Yes. Each job records its warranty status so warranty and paid work stay separate in reports."),
            ("Can customers book a tune-up online?", "Yes. Online booking shows real available time slots and sends reminders."),
            ("Does it handle both engines and cordless tools?", "Yes. The same template captures engine hours for small engines and voltage for power tools."),
        ],
    },
    {
        "slug": "marine-repair-software", "vs": [("shopmonkey", "Shopmonkey"), ("orderry", "Orderry")], "noun": "Marine repair",
        "template": "Marine",
        "title": "Marine Service & Boat Repair Software | Tekly",
        "desc": "Marine service software for boatyards and outboard shops. HIN and engine serial intake, AI estimates, customer approvals, parts and payroll.",
        "h1": "Marine service and boat repair software",
        "lead": "Marine service software built for boatyards and outboard shops, from winterising to a full repower. Track the hull ID and each engine separately, draft estimates with AI and get approvals before the boat comes out of the water.",
        "fields": [("HIN", "Hull identification number"), ("Engine serial", "Each engine tracked separately"), ("Make, model and year", "e.g. 19 ft centre console"), ("Length", "In feet"), ("Registration", "State or national"), ("Condition photos", "Taken at haul-out")],
        "features": [
            ("AI estimates from the complaint", "“Won't idle, stalls in gear” becomes a diagnostic, fuel service and parts — at your rates."),
            ("Every engine on its own record", "Twin outboards? Each engine keeps its own serial and service history under the vessel."),
            ("Approval before you start", "Owners approve each line from their phone, wherever they are."),
            ("Parts booked to the job", "Impellers, filters and lower-unit parts are written off to the right boat on arrival."),
            ("Seasonal scheduling", "Winterising and spring commissioning booked online with reminders."),
            ("Payroll from closed jobs", "Technician hours and commission post automatically when the job closes."),
        ],
        "who": ["Boatyards and marinas with a service department", "Outboard and inboard engine shops", "Fibreglass and gelcoat repair shops", "Trailer service and repair", "Dealers with a service bench"],
        "steps": [
            ("Check in", "Record the HIN, each engine's serial and the boat's length, with condition photos at haul-out."),
            ("Estimate", "On Pro, Tekly AI turns “won't idle, stalls in gear” into a diagnostic, fuel service and parts at your rates. You review and send."),
            ("Approval", "Owners approve each line from their phone, wherever they are, and sign before you start."),
            ("Parts", "Impellers, filters and lower-unit parts are charged to the right boat when they arrive."),
            ("Pickup and pay", "Payment at the dock office or by link. Technician hours and commission post when the job closes."),
        ],
        "photo": ("boat-engine", "A mechanic working on an inflatable boat's outboard engine on the water", 'Photo: <a href="https://commons.wikimedia.org/wiki/File:Engineer_person_working_on_boat_engine.jpg" rel="noopener">Steve Hillebrand, U.S. Fish and Wildlife Service</a>, public domain'),
        "more_faq": [
            ("How much does marine service software cost?", "Tekly starts at $29 a month for up to 2 people. Shop is $69 a month and adds inventory, scanning and reports; Pro is $129 and adds purchase orders, payroll and AI estimate drafting. Extra locations are $49 on Shop and $79 on Pro."),
            ("Can I run a marine shop and another trade in one account?", "Yes. Use the Marine template alongside Vehicles, Electronics or Tools & small engine in one account, with one login."),
            ("Can I move my existing customer and boat records?", "Yes. Send an export or spreadsheet and we bring customers, vessels and stock across, free on every plan."),
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
      <div><h2 class="foot-h">Product</h2><ul><li><a href="/product/">Product tour</a></li><li><a href="/pricing/">Pricing</a></li><li><a href="/product/#features">All features</a></li></ul></div>
      <div><h2 class="foot-h">Software for</h2><ul>{nav_links(current)}</ul></div>
      <div><h2 class="foot-h">Compare</h2><ul>{"".join(f'<li><a href="/compare/tekly-vs-{c["slug"]}/">Tekly vs {e(c["name"])}</a></li>' for c in COMPETITORS)}<li><a href="/compare/">All comparisons</a></li><li><a href="/guides/choose-repair-shop-software/">How to choose</a></li></ul></div>
      <div><h2 class="foot-h">Company</h2><ul><li><a href="/about/">About us</a></li><li><a href="/changelog/">What's new</a></li><li><a href="/#start">Book a demo</a></li><li><a data-mail href="/#start">Contact</a></li><li><a href="/pricing/#faq">FAQ</a></li></ul></div>
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
    all_faq = p["faq"] + p.get("more_faq", [])
    ld = [
        {"@context": "https://schema.org", "@type": "WebPage", "name": p["title"], "url": url, "description": p["desc"],
         "isPartOf": {"@id": DOMAIN + "/#website"}},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Tekly", "item": DOMAIN + "/"},
            {"@type": "ListItem", "position": 2, "name": p["h1"], "item": url}]},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in all_faq]},
    ]
    fields = "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in p["fields"])
    feats = "".join(f'<article class="card rv"><h3>{e(t)}</h3><p>{e(d)}</p></article>' for t, d in p["features"])
    faq = "".join(f'<details><summary>{e(q)}<svg aria-hidden="true"><use href="#plus"/></svg></summary><p>{e(a)}</p></details>' for q, a in all_faq)
    who = "".join(f"<li>{e(w)}</li>" for w in p["who"])
    steps = "".join(f'<li class="rv"><span class="mono step-n">{i:02d}</span><div><h3>{e(t)}</h3><p>{e(d)}</p></div></li>'
                    for i, (t, d) in enumerate(p["steps"], 1))
    ph, alt, credit = p["photo"]
    photo = (f'<figure class="sub-photo rv"><img src="/assets/photos/{ph}-960.webp" srcset="/assets/photos/{ph}-600.webp 600w, '
             f'/assets/photos/{ph}-960.webp 960w" sizes="(max-width: 900px) 92vw, 34rem" width="960" height="640" alt="{html.escape(alt)}" '
             f'loading="lazy" decoding="async"><figcaption>{credit}</figcaption></figure>')
    others = "".join(f'<li><a class="link" href="/{o["slug"]}/">{e(o["h1"])}</a></li>' for o in PAGES if o is not p)
    main = f'''
<section class="hero sub-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Tekly</a><span aria-hidden="true">/</span><span>{e(p["h1"])}</span></nav>
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

<section class="sec">
  <div class="wrap two-col">
    <div class="rv">
      <p class="eyebrow">Who it's for</p>
      <h2 class="h-section">Built for {e(p["noun"].lower())} shops of every size.</h2>
      <p class="lede">From a one-person bench to several locations, on one {e(p["template"])} template. Shops that fix more than one kind of thing can add other templates to the same account.</p>
      <ul class="who">{who}</ul>
      <p class="fine-print">Switching from another system? {" · ".join(f'<a class="link" href="/compare/tekly-vs-{x}/">Tekly vs {e(n)}</a>' for x, n in p["vs"])}</p>
    </div>
    {photo}
  </div>
</section>

<section class="sec sec-tight">
  <div class="wrap">
    <div class="sec-head"><p class="eyebrow rv">How a job runs</p><h2 class="h-section rv">From check-in to paid, in five steps.</h2></div>
    <ol class="job-steps">{steps}</ol>
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
    <div><p class="eyebrow">Pricing</p><h2 class="h-sub">Plans from $29 a month.</h2><p class="lede">Solo is $29 for up to 2 people. Shop is $69 for 4 people shared across locations ($49 per extra location, $6 per extra person) and adds inventory, scanning, the time clock and reports. Pro is $129 for the first location ($79 each extra) with 8 people, plus purchase orders, payroll, AI estimate drafting and accounting sync. Every trial includes all Pro features for 14 days, no card.</p></div>
    <div class="sub-price-ctas"><a class="btn btn-brand btn-lg" data-app="signup" data-cta="pricing_trial" href="/#start">Start free trial</a><a class="link" href="/pricing/">Compare plans</a></div>
  </div></div>
</section>

<section class="sec sec-tight">
  <div class="wrap faq">
    <div><p class="eyebrow">Questions</p><h2 class="h-sub">{e(p["noun"])}, answered.</h2><p class="lede">More questions? <a class="link" href="/pricing/#faq">Read the full FAQ</a></p></div>
    <div>{faq}</div>
  </div>
</section>

<section class="sec sec-tight sec-cream">
  <div class="wrap"><p class="eyebrow">Other templates</p><ul class="others">{others}</ul></div>
</section>
'''
    return shell(p, url, main, ld)


def shell(p, url, main, ld):
    """Wraps a page body in the shared site shell (tools/build_site.py: header in a navy title card,
    floating bar, footer, menu, demo form, assets/site.css + site.js), so these pages look and
    behave like home, product and pricing. The body keeps its own markup, styled by
    assets/sub.css (styles.css scoped to .legacy) and wrapped in rounded content cards."""
    path = url[len(DOMAIN):] or "/"
    m = re.search(r'<section class="hero sub-hero">.*?<nav class="crumbs"[^>]*>(.*?)</nav>\s*<h1[^>]*>(.*?)</h1>\s*'
                  r'<p class="hero-sub">(.*?)</p>.*?<p class="mono hero-fine">(.*?)</p>\s*</div>\s*</section>', main, re.S)
    if m:
        crumbs, h1, lead, fine = m.groups()
        main = main[:m.start()] + main[m.end():]
    else:   # legal pages: the title lives in the body
        t = re.search(r'<p class="eyebrow">[^<]*</p><h1 class="h-section">(.*?)</h1>', main)
        h1, lead, fine = t.group(1), "", ""
        main = main.replace(t.group(0), "")
        crumbs = f'<a href="/">Tekly</a><span aria-hidden="true">/</span><span>{h1}</span>'
    crumbs = re.sub(r'<span>([^<]*)</span>$', r'<span aria-current="page">\1</span>', crumbs.strip())
    ctas = ('<div class="hero-ctas phero-ctas"><a class="btn btn-solid" data-app="signup" href="#start" data-spring="nudge">Start free trial '
            '<svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a><button class="btn btn-outline" type="button" data-open-demo>Book a demo</button></div>'
            + (f'<p class="hero-fine">{fine}</p>' if fine else ""))
    top = build_site.phero("", h1, lead, ctas if m else "", crumbs_html=crumbs)
    if not lead:
        top = top.replace('    <p class="phero-lede" data-hero-fade></p>\n', "")
    nav = "/compare/" if path.startswith("/compare/") else "/#industries" if p.get("slug") in {x["slug"] for x in PAGES} | {"multi-location-repair-shop-software"} else ""
    page = {"path": path, "title": html.unescape(p["title"]), "desc": html.unescape(p["desc"]), "nav": nav, "ld": ld if isinstance(ld, list) else [ld],
            "css": f'  <link rel="stylesheet" href="/assets/sub.css?v={build_site.ver("assets/sub.css")}">\n'}
    return build_site.render(page, top + wrap_legacy(main))


def wrap_legacy(main):
    """Each top-level <section> of the old markup becomes a rounded content card."""
    out, pos = [], 0
    for m in re.finditer(r"<section\b[^>]*>|</section>", main):
        pass
    depth, start, cls = 0, None, ""
    for m in re.finditer(r"<section\b([^>]*)>|</section>", main):
        if m.group(0).startswith("<section"):
            if depth == 0:
                out.append(main[pos:m.start()]); start = m.start()
                cls = re.search(r'class="([^"]*)"', m.group(1) or "")
                cls = cls.group(1) if cls else ""
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                tint = " tint" if "sec-cream" in cls else ""
                out.append(f'<div class="card-sec legacy{tint}">\n{main[start:m.end()]}\n</div>\n\n'); pos = m.end()
    out.append(main[pos:])
    return "".join(out)


def shared_class_reset(site_css, old_css, scope=".legacy"):
    """Class names both stylesheets define (.sec-head, .eyebrow, .lede, .card…) would pick up the
    new site's rules inside old page bodies. Reset them there first; the scoped old rules follow."""
    names = lambda css: set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"\{[^{}]*\}", "{}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))))
    shared = sorted((names(site_css) & names(old_css)) - {"js", "sub", "legacy", "reduced", "on", "shown", "rv"})
    return f"{scope} :is({', '.join('.' + n for n in shared)}) {{ all: revert; }}\n"


def scope_css(css, scope=".legacy"):
    """styles.css with every rule limited to `scope` (`:root`, `html`, `body` and `.sub` become the
    scope itself), for old page bodies shown inside the new site shell."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    def sel(one):
        one = one.strip()
        if not one:
            return one
        if one.startswith(".js "):
            return ".js " + sel(one[4:])
        for head in (":root", "html", "body.sub", "body", ".sub"):
            if one == head:
                return scope
            if one.startswith(head + " ") or one.startswith(head + ":") and head != ":root":
                return scope + one[len(head):]
        if one.startswith("::"):
            return f"{scope} {one}"
        return f"{scope} {one}"

    out, i = [], 0
    while i < len(css):
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        depth, k = 1, j + 1
        while depth and k < len(css):
            depth += {"{": 1, "}": -1}.get(css[k], 0); k += 1
        block = css[j + 1:k - 1]
        if head.startswith("@media") or head.startswith("@supports"):
            out.append(f"{head} {{ {scope_css(block, scope)} }}")
        elif head.startswith("@"):
            out.append(f"{head} {{{block}}}")
        else:
            out.append(", ".join(sel(x) for x in head.split(",")) + " {" + block + "}")
        i = k
    return "\n".join(out)


LEGACY_FIXES = """
/* Inside the shared shell: the card supplies the spacing and width. */
.legacy .sec { padding: 0; background: none; border: 0; }
.legacy .sec + .sec { margin-top: 4rem; }
.legacy .wrap { max-width: none; padding: 0; }
.legacy { color: var(--ink-2); }
.js .legacy .rv { transition-duration: .55s; }
/* The card itself keeps the shell's shape (styles.css redefines --r-lg inside .legacy). */
.card-sec.legacy { border-radius: 0; }
.legacy .eyebrow { display: inline-block; font-family: "Geist", system-ui, sans-serif; font-size: 14px; font-weight: 500; letter-spacing: 0; text-transform: none; color: #1c5fd6; }
.legacy .eyebrow::before { content: none; }
"""

def faq_ld(items):
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in items]}


def crumbs_ld(trail):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": n, "item": u} for i, (n, u) in enumerate(trail, 1)]}


def faq_html(items):
    return "".join(f'<details><summary>{e(q)}<svg aria-hidden="true"><use href="#plus"/></svg></summary><p>{e(a)}</p></details>'
                   for q, a in items)


def sub_hero(crumb, h1, lead, fine):
    return f'''
<section class="hero sub-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb">{crumb}</nav>
    <h1 class="h-display">{e(h1)}</h1>
    <p class="hero-sub">{e(lead)}</p>
    <div class="hero-ctas"><a class="btn btn-brand btn-lg" data-app="signup" data-cta="hero_trial" href="/#start">Start free trial</a><a class="btn btn-ghost btn-lg" href="/#start" data-cta="hero_demo">Book a demo</a></div>
    <p class="mono hero-fine">{fine}</p>
  </div>
</section>'''


def compare_page(c):
    url = f"{DOMAIN}/compare/tekly-vs-{c['slug']}/"
    name = c["name"]
    faq = [
        (f"Is Tekly cheaper than {name}?", f"Going by published prices as of {CHECKED}: Tekly starts at $29 a month (Solo, up to 2 people), or $69 for 4 people on Shop. {name}'s published starting price is {c['rows'][0][2]}. Compare what your whole team would pay, including extra people and locations."),
        (f"What is a good alternative to {name}?", f"It depends on your trade and team. Tekly is an alternative to {name} for {c['alt_for']}: published prices from $29 a month, a 14-day free trial with no card, and free data migration. The comparison above lists where {name} may suit you better."),
        (f"Can I switch from {name} to Tekly?", f"Yes. Send an export from {name} or your spreadsheets and Tekly brings customers, devices or vehicles and stock across, free on every plan. You can take your first job the day you sign up."),
        (f"Where do these {name} details come from?", f"From {name}'s own public website, checked on {CHECKED}. Prices and features change, so check their site for current terms."),
    ]
    ld = [{"@context": "https://schema.org", "@type": "WebPage", "name": c["title"], "url": url, "description": c["desc"],
           "isPartOf": {"@id": DOMAIN + "/#website"}, "dateModified": TODAY},
          crumbs_ld([("Tekly", DOMAIN + "/"), ("Compare", DOMAIN + "/compare/"), (f"Tekly vs {name}", url)]),
          faq_ld(faq)]
    rows = "".join(f'<tr><th scope="row">{e(k)}</th><td data-col="Tekly">{e(t)}</td><td data-col="{e(name)}">{e(o)} <a class="src" href="{src}" rel="nofollow noopener" target="_blank">source</a></td></tr>'
                   for k, t, o, src in c["rows"])
    them = "".join(f"<li>{e(x)}</li>" for x in c["them_fits"])
    us = "".join(f"<li>{e(x)}</li>" for x in c["tekly_fits"])
    others = "".join(f'<li><a class="link" href="/compare/tekly-vs-{o["slug"]}/">Tekly vs {e(o["name"])}</a></li>' for o in COMPETITORS if o is not c)
    main = sub_hero(f'<a href="/">Tekly</a><span aria-hidden="true">/</span><a href="/compare/">Compare</a><span aria-hidden="true">/</span><span>Tekly vs {e(name)}</span>',
                    f"Tekly vs {name}", c["lead"], f"{name} details from their public website, checked {CHECKED}") + f'''
<section class="sec">
  <div class="wrap">
    <div class="sec-head"><p class="eyebrow rv">Looking for a {e(name)} alternative?</p><h2 class="h-section rv">Tekly and {e(name)}, by the published facts.</h2>
      <p class="lede rv">{e(c["about"])} Everything in the {e(name)} column comes from their own website on {CHECKED}; follow each source link for current terms.</p></div>
    <div class="cmp-wrap rv"><table class="cmp-table">
      <caption class="sr">Tekly compared with {e(name)}</caption>
      <thead><tr><th scope="col">What to compare</th><th scope="col">Tekly</th><th scope="col">{e(name)}</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    <p class="fine-print">Tekly plans: {e(TEKLY_PRICES)}</p>
  </div>
</section>

<section class="sec sec-cream">
  <div class="wrap fit-grid">
    <div class="fit rv"><h2 class="h-sub">{e(name)} may suit you better if…</h2><ul>{them}</ul></div>
    <div class="fit fit-us rv"><h2 class="h-sub">Tekly may suit you better if…</h2><ul>{us}</ul></div>
  </div>
</section>

<section class="sec sec-tight">
  <div class="wrap faq">
    <div><p class="eyebrow">Questions</p><h2 class="h-sub">Switching from {e(name)}.</h2><p class="lede">Try every Pro feature free for 14 days, no card. We move your data across for free.</p>
      <div class="hero-ctas"><a class="btn btn-brand btn-lg" data-app="signup" data-cta="compare_trial" href="/#start">Start free trial</a><a class="link" href="/guides/choose-repair-shop-software/">How to choose repair shop software</a></div></div>
    <div>{faq_html(faq)}</div>
  </div>
</section>

<section class="sec sec-tight sec-cream">
  <div class="wrap"><p class="eyebrow">Other comparisons</p><ul class="others">{others}</ul></div>
</section>
'''
    return shell({"title": c["title"], "desc": c["desc"], "slug": ""}, url, main, ld)


def compare_hub():
    url = f"{DOMAIN}/compare/"
    p = {"title": "Repair Shop Software Alternatives, Compared | Tekly", "slug": "",
         "desc": "Tekly compared with RepairDesk, RepairShopr, Shopmonkey, Tekmetric and Orderry: published prices, approvals, trades and trials, with sources."}
    ld = [{"@context": "https://schema.org", "@type": "CollectionPage", "name": p["title"], "url": url, "description": p["desc"],
           "isPartOf": {"@id": DOMAIN + "/#website"}},
          crumbs_ld([("Tekly", DOMAIN + "/"), ("Compare", url)]),
          {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
              {"@type": "ListItem", "position": i, "url": f"{DOMAIN}/compare/tekly-vs-{c['slug']}/", "name": f"Tekly vs {c['name']}"}
              for i, c in enumerate(COMPETITORS, 1)]}]
    cards = "".join(f'''<a class="card rv cmp-card" href="/compare/tekly-vs-{c["slug"]}/"><h3>Tekly vs {e(c["name"])}</h3><p>{e(c["about"])}</p><p class="fine-print"><b>{e(c["name"])}:</b> {e(c["rows"][0][2])}<br><b>Tekly:</b> {e(c["rows"][0][1])}</p><span class="link">Read the comparison</span></a>''' for c in COMPETITORS)
    main = sub_hero('<a href="/">Tekly</a><span aria-hidden="true">/</span><span>Compare</span>', "Repair shop software alternatives, compared",
                    "How Tekly compares with the repair shop software shops most often ask us about — prices, approvals, trades and trials, taken from each vendor's own website.",
                    f"Vendor details checked {CHECKED}") + f'''
<section class="sec"><div class="wrap"><div class="ai-grid">{cards}</div>
<p class="fine-print">Each comparison links to the vendor's own pages. Prices and features change; check the vendor's site for current terms.</p></div></section>'''
    return shell(p, url, main, ld)


def guide_page():
    g = GUIDE
    url = f"{DOMAIN}/{g['slug']}/"
    ld = [{"@context": "https://schema.org", "@type": "Article", "headline": g["h1"], "description": g["desc"], "url": url,
           "datePublished": "2026-10-23", "dateModified": TODAY, "image": DOMAIN + "/assets/og.jpg",
           "author": {"@type": "Organization", "name": "Tekly", "url": DOMAIN + "/"},
           "publisher": {"@id": DOMAIN + "/#org"}, "isPartOf": {"@id": DOMAIN + "/#website"}},
          crumbs_ld([("Tekly", DOMAIN + "/"), (g["h1"], url)]), faq_ld(g["faq"])]
    secs = "".join(f'<section class="guide-sec rv" id="s{i}"><h2 class="h-sub"><span class="mono step-n">{i:02d}</span> {e(t)}</h2><p>{e(body)}</p></section>'
                   for i, (t, body, _) in enumerate(g["sections"], 1))
    toc = "".join(f'<li><a href="#s{i}">{e(t)}</a></li>' for i, (t, _, _) in enumerate(g["sections"], 1)) + '<li><a href="#compare">Comparing specific products</a></li>'
    main = sub_hero('<a href="/">Tekly</a><span aria-hidden="true">/</span><span>Guides</span>', g["h1"], g["lead"],
                    f"Updated {CHECKED} · about 5 minutes to read") + f'''
<section class="sec"><div class="wrap guide">
  <aside class="guide-toc"><p class="eyebrow">The checklist</p><ol>{toc}</ol></aside>
  <div class="guide-body">{secs}
    <section class="guide-sec rv" id="compare"><h2 class="h-sub">Comparing specific products</h2><p>We've put the published facts side by side for the systems shops ask us about most: {", ".join(f'<a class="link" href="/compare/tekly-vs-{c["slug"]}/">Tekly vs {e(c["name"])}</a>' for c in COMPETITORS)}.</p></section>
  </div>
</div></section>

<section class="sec sec-tight sec-cream">
  <div class="wrap faq">
    <div><p class="eyebrow">Questions</p><h2 class="h-sub">Repair shop software, answered.</h2></div>
    <div>{faq_html(g["faq"])}</div>
  </div>
</section>
'''
    return shell({"title": g["title"], "desc": g["desc"], "slug": ""}, url, main, ld)


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


MULTI = {
    "slug": "multi-location-repair-shop-software",
    "title": "Multi-Location Repair Shop Software | Tekly",
    "desc": "Run every repair shop location in one account: shared team seats, stock across sites, numbers for each location. Extra locations from $49/mo.",
    "h1": "Multi-location repair shop software",
    "lead": "Two counters across town, a depot and a storefront, or a phone shop next to a boatyard: Tekly runs every location and every trade in one account, with one login.",
    "faq": [
        ("How is multi-location pricing worked out?", "You pay for the first location on your plan, then a flat price for each extra one: Shop is $69 a month plus $49 per extra location, Pro is $129 a month plus $79 per extra location. Solo covers one location."),
        ("Can my team work at more than one location?", "Yes. Team members are shared across all your locations: 4 on Shop and 8 on Pro, and each extra person is $6 a month."),
        ("Can I move stock between locations?", "Every location on Shop and Pro keeps its own stock, bins and serial numbers. Transfers between locations are on Pro."),
        ("Can each location run a different trade?", "Yes. Use the Electronics, Vehicles, Tools & small engine and Marine templates side by side in one account."),
        ("Can I see each location's numbers separately?", "Yes. Profit per job, per technician and per location is always current, and roles limit who sees what."),
        ("What if I have ten or more locations?", "Shop and Pro add locations at the same flat price, as many as you need. Franchises, depots and fleets can ask us about custom terms."),
    ],
}


def multi_location_page():
    p, cfg = MULTI, build_site.site_config()
    url = f"{DOMAIN}/{p['slug']}/"
    plans = {x["id"]: x for x in cfg["plans"]}
    shop, pro = plans["shop"], plans["pro"]
    cost = lambda pl, n: pl["monthly"] + (n - 1) * pl["extraLocation"]
    rows = "".join(f'<tr><th scope="row">{n} location{"s" if n > 1 else ""}</th><td>${cost(shop, n):,}/mo <span class="dim">· 4 people shared</span></td>'
                   f'<td>${cost(pro, n):,}/mo <span class="dim">· 8 people shared</span></td></tr>' for n in (1, 2, 3, 5))
    ld = [{"@context": "https://schema.org", "@type": "WebPage", "name": p["h1"], "url": url, "description": p["desc"],
           "isPartOf": {"@id": DOMAIN + "/#website"}, "about": {"@id": DOMAIN + "/#software"}},
          crumbs_ld([("Tekly", DOMAIN + "/"), (p["h1"], url)]), faq_ld(p["faq"])]
    trades = "".join(f'<li><a class="link" href="/{o["slug"]}/">{e(o["h1"])}</a></li>' for o in PAGES)
    main = sub_hero(f'<a href="/">Tekly</a><span aria-hidden="true">/</span><span>{e(p["h1"])}</span>', p["h1"], p["lead"],
                    f'<span data-trial>14</span>-day trial · no card · extra locations from ${shop["extraLocation"]}/mo') + f'''
<section class="sec">
  <div class="wrap">
    <div class="sec-head"><p class="eyebrow rv">One account</p><h2 class="h-section rv">Every location, every trade, one login.</h2>
      <p class="lede rv">Add a location when you open one. Customers, jobs and history stay in the same account, so the owner sees the whole business and each counter sees its own work.</p></div>
    <div class="ai-grid" style="grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr))">
      <article class="card rv"><h3>Several trades, one login</h3><p>Use the Electronics, Vehicles, Tools &amp; small engine and Marine templates side by side.</p></article>
      <article class="card rv"><h3>Team seats that travel</h3><p>Shop's 4 team members and Pro's 8 are shared across your locations.</p></article>
      <article class="card rv"><h3>Stock per site</h3><p>Each location keeps its own stock, bins and serial numbers. Pro moves stock between them.</p></article>
      <article class="card rv"><h3>Numbers for each location</h3><p>Profit per job, per technician and per location, with roles that limit who sees what.</p></article>
    </div>
  </div>
</section>

<section class="sec sec-cream">
  <div class="wrap">
    <div class="sec-head"><p class="eyebrow rv">What it costs</p><h2 class="h-section rv">A flat price for each extra location.</h2>
      <p class="lede rv">Shop is ${shop["monthly"]} a month for the first location and ${shop["extraLocation"]} for each one after. Pro is ${pro["monthly"]} and ${pro["extraLocation"]}. No setup fee, no contract.</p></div>
    <div class="cmp-wrap rv"><table class="cmp-table">
      <caption class="sr">Monthly cost by number of locations</caption>
      <thead><tr><th scope="col">Locations</th><th scope="col">Shop</th><th scope="col">Pro</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    <p class="fine-print">Each extra person is ${shop["extraSeat"]} a month, past 4 on Shop or 8 on Pro. Pro adds purchase orders, payroll, stock transfers between locations and AI estimate drafting. <a class="link" href="/pricing/">Compare plans</a></p>
  </div>
</section>

<section class="sec sec-tight">
  <div class="wrap faq">
    <div><p class="eyebrow">Questions</p><h2 class="h-sub">Running several locations, answered.</h2><p class="lede">More questions? <a class="link" href="/pricing/#faq">Read the full FAQ</a></p></div>
    <div>{faq_html(p["faq"])}</div>
  </div>
</section>

<section class="sec sec-tight sec-cream">
  <div class="wrap"><p class="eyebrow">Templates for each trade</p><ul class="others">{trades}</ul></div>
</section>
'''
    return shell(p, url, main, ld)


def main():
    old_css, site_css = (ROOT / "styles.css").read_text(encoding="utf-8"), (ROOT / "assets" / "site.css").read_text(encoding="utf-8")
    (ROOT / "assets" / "sub.css").write_text("/* Generated by tools/build_pages.py from styles.css — do not edit. */\n"
        + shared_class_reset(site_css, old_css) + scope_css(old_css) + LEGACY_FIXES, encoding="utf-8")
    for p in PAGES:
        d = ROOT / p["slug"]
        d.mkdir(exist_ok=True)
        (d / "index.html").write_text(page(p), encoding="utf-8")
    d = ROOT / MULTI["slug"]
    d.mkdir(exist_ok=True)
    (d / "index.html").write_text(multi_location_page(), encoding="utf-8")
    for c in COMPETITORS:
        d = ROOT / "compare" / f"tekly-vs-{c['slug']}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(compare_page(c), encoding="utf-8")
    (ROOT / "compare").mkdir(exist_ok=True)
    (ROOT / "compare" / "index.html").write_text(compare_hub(), encoding="utf-8")
    gd = ROOT / GUIDE["slug"]
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "index.html").write_text(guide_page(), encoding="utf-8")
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
    urls = ([("/", "1.0"), ("/product/", "0.9"), ("/pricing/", "0.9"), ("/about/", "0.5"), ("/changelog/", "0.6")] + [(f"/{p['slug']}/", "0.8") for p in PAGES] + [(f"/{MULTI['slug']}/", "0.8")]
            + [("/compare/", "0.6")] + [(f"/compare/tekly-vs-{c['slug']}/", "0.7") for c in COMPETITORS]
            + [(f"/{GUIDE['slug']}/", "0.7")]
            + [(f"/{d['slug']}/", "0.3") for d in LEGAL])
    (ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{DOMAIN}{u}</loc><lastmod>{lastmod(u)}</lastmod></url>\n" for u, _ in urls)
        + "</urlset>\n", encoding="utf-8")
    (ROOT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: /tools/\nDisallow: /v2/\n\nSitemap: {DOMAIN}/sitemap.xml\n", encoding="utf-8")
    (ROOT / "site.webmanifest").write_text(json.dumps({
        "name": "Tekly", "short_name": "Tekly", "start_url": "/", "display": "standalone",
        "background_color": "#ffffff", "theme_color": "#003dff",
        "icons": [{"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/assets/icon-512.png", "sizes": "512x512", "type": "image/png"}]}, indent=2), encoding="utf-8")
    print("built", len(PAGES), "pages + sitemap.xml, robots.txt, site.webmanifest")


if __name__ == "__main__":
    main()
