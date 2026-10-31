"""Comparison and guide pages, rendered by build_pages.py.

Rules for these pages (they are about other companies, so accuracy is the whole point):
- Every competitor fact comes from that vendor's own public site, with the URL, as checked on
  CHECKED. Re-check before changing a number; prices change.
- Say what their site says. Never "X doesn't have Y" unless the vendor says so; if their site
  doesn't mention something, leave it out rather than imply it's missing.
- Each page says plainly when the other product may suit a shop better.
"""

CHECKED = "23 October 2026"

TEKLY_PRICES = ("Solo $29/mo (up to 2 people, 150 jobs a month) · Shop $69/mo for 4 people shared across "
                "locations, $49 per extra location · Pro $129/mo for 8 people, $6 per extra person on both, "
                "$79 per extra location. Month to month; yearly gets 2 months free.")

COMPETITORS = [
    {
        "slug": "repairdesk", "alt_for": "phone, computer and electronics repair shops", "name": "RepairDesk",
        "title": "Tekly vs RepairDesk: Repair Shop Software Compared",
        "desc": "A RepairDesk alternative for phone and electronics repair shops: published prices, estimate approvals, trades covered, AI and payroll, side by side.",
        "lead": "Both are cloud repair shop software with POS, inventory and customer notifications. RepairDesk is built around phone, computer and specialty retail repair; Tekly covers electronics, vehicles, small engine and marine in one account, with line-by-line approvals on the customer's phone.",
        "about": "RepairDesk describes itself as cloud-based point-of-sale software for small and medium-sized repair shops, and lists cell phone, computer, small engine, power tool, jewellery, watch, bicycle, drone, camera, heavy-duty and mail-in repair.",
        "rows": [
            ("Published starting price", "$29/mo (Solo, up to 2 people), billed monthly", "From $79/mo billed annually or $99/mo billed quarterly (Essential, 5 users, per store)", "https://www.repairdesk.co/pricing/"),
            ("A plan for 8 people", "$93/mo (Shop: 4 people included + 4 extra at $6) or $129/mo (Pro, 8 people)", "From $119/mo billed annually or $149/mo billed quarterly (Growth, 8 users)", "https://www.repairdesk.co/pricing/"),
            ("Free trial", "14 days, every Pro feature, no card", "Free trial offered; length not stated on the pricing page", "https://www.repairdesk.co/pricing/"),
            ("Customer estimate approval", "Line by line on the customer's phone, with a signature; extra work waits for their tick", "Customers can approve, decline or request changes to an estimate from email", "https://help.repairdesk.co/portal/en/kb/articles/how-can-i-create-and-manage-an-estimate"),
            ("Payroll", "Payroll and commissions from closed jobs (Pro)", "Employee management that can process payroll", "https://www.repairdesk.co/"),
            ("AI", "Estimate drafting (Pro), job summaries, reply suggestions, call insights", "ARIA, an AI assistant that backs up missed calls", "https://www.repairdesk.co/"),
            ("Trades", "Electronics, vehicles, small engine & tools, marine — several in one account", "Phones, computers and specialty retail repair, plus small engine and power tools", "https://www.repairdesk.co/"),
        ],
        "them_fits": [
            "You repair jewellery, watches, drones or cameras — trades RepairDesk lists and Tekly has no template for.",
            "You want an AI assistant that answers missed calls (RepairDesk's ARIA).",
        ],
        "tekly_fits": [
            "You want month-to-month billing: Tekly is $29 a month billed monthly for up to 2 people, against RepairDesk Essential from $79/mo billed annually or $99/mo quarterly for 5 users.",
            "Customers should approve or decline each line of an estimate on their phone and sign, including extra work found mid-repair.",
            "You fix vehicles or boats as well as devices, and want VIN and HIN intake fields in the same account.",
            "Tekly AI drafts the estimate from the customer's words on Pro. If that matters to you, ask RepairDesk how its AI handles estimates.",
        ],
    },
    {
        "slug": "repairshopr", "alt_for": "phone, computer and electronics repair shops", "name": "RepairShopr",
        "title": "Tekly vs RepairShopr: Repair Shop Software Compared",
        "desc": "A RepairShopr alternative: published plan prices, user and ticket limits, trial length, estimates, POS and inventory for repair shops, side by side.",
        "lead": "RepairShopr and Tekly both run tickets, estimates, POS and inventory for repair shops. RepairShopr also serves IT and managed-service providers; Tekly focuses on bench repair across electronics, vehicles, small engine and marine.",
        "about": "RepairShopr lists computer, cell phone, electronics, IT/MSP, general repair, watch, scuba, musical instrument, small engine and lawn care shops among the businesses it serves.",
        "rows": [
            ("Published starting price", "$29/mo (Solo, up to 2 people, 150 jobs a month)", "$69.99/mo monthly or $59.99/mo annual (Starter: 1 user, 1 location, 75 tickets and invoices a month)", "https://www.repairshopr.com/pricing"),
            ("Bigger team, one location", "$93/mo (Shop: 4 people + 4 extra at $6) or $129/mo (Pro: 8 people), unlimited jobs", "$139.99/mo monthly or $129.99/mo annual (Repair Shop: 10 users)", "https://www.repairshopr.com/pricing"),
            ("Two or more locations", "Shop $69 + $49 per extra location; Pro $129 + $79 per extra location", "$149.99/mo monthly or $139.99/mo annual (Big Chain: 10 users, 2+ locations)", "https://www.repairshopr.com/pricing"),
            ("Free trial", "14 days, every Pro feature, no card", "30-day free trial", "https://www.repairshopr.com/?p=8587"),
            ("Estimates", "AI-drafted estimates on Pro; line-by-line approval and signature on the customer's phone", "Estimates on all plans", "https://www.repairshopr.com/pricing"),
            ("Time clock", "On Shop and Pro", "On all plans", "https://www.repairshopr.com/pricing"),
        ],
        "them_fits": [
            "You run an IT or managed-service business as well as a repair bench — RepairShopr lists IT/MSP work.",
            "You'd like a longer trial: RepairShopr offers 30 days.",
        ],
        "tekly_fits": [
            "You're a small shop: Tekly's Solo plan is $29 a month for 2 people, and Shop is $69 for 8.",
            "Customers should approve or decline each line of an estimate on their phone and sign.",
            "You fix boats as well as devices or vehicles, with intake fields for each trade (VIN, engine hours, HIN) in one account.",
            "Payroll and technician commissions come straight from closed jobs on Pro. If that matters, ask RepairShopr how they handle it.",
        ],
    },
    {
        "slug": "shopmonkey", "alt_for": "auto repair shops", "name": "Shopmonkey",
        "title": "Tekly vs Shopmonkey: Auto Repair Shop Software Compared",
        "desc": "A Shopmonkey alternative for auto repair shops: published prices, users included, trial, approvals, inventory, payroll and migration fees compared.",
        "lead": "Shopmonkey is auto repair shop software with estimates, digital authorizations, payments and parts ordering. Tekly covers auto repair alongside electronics, small engine and marine, at a lower published price, with payroll on Pro.",
        "about": "Shopmonkey lists auto repair, tire, quick lube, heavy-duty and wrap-and-detail shops, and has a page for boat repair shops.",
        "rows": [
            ("Published starting price", "$29/mo (Solo, 2 people); $69/mo (Shop, 4 people)", "$239/mo monthly or $215/mo annual (Basic, 3 user licenses)", "https://www.shopmonkey.io/pricing"),
            ("Higher plans", "Pro $129/mo, 8 people", "Clever $399/mo (4 user licenses) and Genius $499/mo (5) monthly", "https://www.shopmonkey.io/pricing"),
            ("Free trial", "14 days, every Pro feature, no card", "No free trial shown; demo available", "https://www.shopmonkey.io/pricing"),
            ("Customer approvals", "Line by line on the customer's phone, with a signature; declined work saved for next visit", "Digital authorizations and e-signatures on estimates, on all plans", "https://www.shopmonkey.io/product/estimates"),
            ("Inventory and purchase orders", "Inventory on Shop; purchase orders on Pro", "Inventory management and purchase orders from the Clever plan", "https://www.shopmonkey.io/pricing"),
            ("Payroll and commissions", "Payroll and commissions from closed jobs (Pro)", "Payments and commission reports on all plans", "https://www.shopmonkey.io/pricing"),
            ("Data migration", "Free on every plan", "One-time $500 data migration fee", "https://www.shopmonkey.io/pricing"),
        ],
        "them_fits": [
            "You want parts lookup and ordering built in — Shopmonkey lists it on every plan.",
            "You run a tire, quick-lube or wrap-and-detail shop, which Shopmonkey targets directly.",
            "You want buy-now-pay-later and Apple Pay / Google Pay listed on every plan.",
        ],
        "tekly_fits": [
            "Price matters: Tekly's Shop plan is $69 a month for 4 people, against Shopmonkey's published $239 for 3.",
            "You want a free trial before talking to sales, and free data migration.",
            "Declined work should be saved and brought back at the customer's next visit.",
            "You also fix bikes, small engines or electronics in the same business, with intake fields for each (IMEI, engine hours, HIN).",
        ],
    },
    {
        "slug": "tekmetric", "alt_for": "auto repair shops", "name": "Tekmetric",
        "title": "Tekly vs Tekmetric: Auto Repair Shop Software Compared",
        "desc": "A Tekmetric alternative for auto repair shops: published per-shop prices, trial, digital approvals, time clock and multi-location costs compared.",
        "lead": "Tekmetric is auto repair shop software priced per shop, not per user. Tekly is priced per plan with people included, covers other trades too, and has a free trial.",
        "about": "Tekmetric serves auto repair and tire shops and says more than 16,000 shops use it.",
        "rows": [
            ("Published starting price", "$29/mo (Solo); $69/mo (Shop, 4 people); $129/mo (Pro, 8 people)", "$199/mo monthly or $179/mo annual (Start), per shop", "https://www.tekmetric.com/pricing"),
            ("Higher plans", "Pro $129/mo, 8 people and unlimited jobs", "Grow $349/mo and Scale $439/mo monthly; Enterprise custom", "https://www.tekmetric.com/pricing"),
            ("How users are charged", "People included per plan; $6 per extra person on Shop and Pro", "Not charged by user or by repair order", "https://www.tekmetric.com/pricing"),
            ("Extra locations", "$49 each on Shop, $79 each on Pro", "Each location needs its own subscription; Multi-Shop Management is +$70/mo per shop", "https://www.tekmetric.com/pricing"),
            ("Free trial", "14 days, every Pro feature, no card", "Free demos and self-guided tours, not free trials", "https://www.tekmetric.com/pricing"),
            ("Customer approvals", "Line by line on the customer's phone, with a signature", "Digital approval with e-signature, by text or email", "https://www.tekmetric.com/pricing"),
            ("Time clock", "On Shop and Pro", "From the Grow plan", "https://www.tekmetric.com/pricing"),
        ],
        "them_fits": [
            "You run a tire shop and want Tekmetric's Tire Suite (+$39/mo per shop).",
            "You want financing on estimates and integrated parts ordering, which Tekmetric lists.",
            "You want auto-only software from a vendor that says more than 16,000 shops use it.",
        ],
        "tekly_fits": [
            "You want to try it first: Tekly has a 14-day free trial; Tekmetric offers free demos and self-guided tours, not free trials.",
            "You're a small or mid-size shop: Shop is $69 a month for 4 people, Pro $129 for 8.",
            "Payroll and technician commissions come straight from closed jobs on Pro. If that matters, ask Tekmetric how they handle it.",
            "You also fix bikes, small engines, boats or electronics.",
        ],
    },
    {
        "slug": "orderry", "alt_for": "electronics, vehicle, small engine and marine repair shops", "name": "Orderry",
        "title": "Tekly vs Orderry: Repair Shop Software Compared",
        "desc": "An Orderry alternative: published prices, employees included, trial length, estimate approvals, payroll and AI for repair shops, side by side.",
        "lead": "Orderry and Tekly are both all-in-one systems for repair businesses with estimates, inventory, payroll and AI features. Orderry also covers field service and HVAC; Tekly focuses on bench repair with more people included in the price.",
        "about": "Orderry describes itself as an all-in-one platform for small businesses, covering electronics and phone repair, auto and truck, small engines and power tools, marine, bicycle, jewellery, HVAC and contractors, field services and retail.",
        "rows": [
            ("Published starting price", "$29/mo (Solo, 2 people, 150 jobs a month)", "$39/mo (Hobby: 2 employees, 1 location, 100 work orders or sales per 30 days)", "https://orderry.com/pricing/"),
            ("Growing team", "$69/mo (Shop: 4 people included, $6 per extra person)", "From $69/mo (Startup: 3 employees included, $6 per extra employee, up to 15)", "https://orderry.com/pricing/"),
            ("Extra locations", "$49 each on Shop, $79 each on Pro", "$49 each on Startup, $79 on Business, $179 on Enterprise", "https://orderry.com/pricing/"),
            ("Free trial", "14 days, every Pro feature, no card", "7 days, no card", "https://orderry.com/pricing/"),
            ("Customer estimate approval", "Line by line on the customer's phone, with a signature", "Estimates approved online with e-signature and converted to jobs", "https://orderry.com/pricing/"),
            ("Payroll", "Payroll and commissions from closed jobs (Pro)", "Payroll covering hours, commissions and bonuses", "https://orderry.com/pricing/"),
            ("AI", "Estimate drafting (Pro), job summaries, reply suggestions, call insights", "Call and voicemail transcription, sentiment, suggested chat replies, products from photos", "https://orderry.com/pricing/"),
        ],
        "them_fits": [
            "You run field service, HVAC or contracting work — Orderry lists these and Tekly doesn't.",
            "You need a very large team on one plan (Orderry's Enterprise plan goes up to 1,500 employees).",
            "You want payroll and commissions on every plan — Orderry lists them on all plans; Tekly has payroll on Pro.",
        ],
        "tekly_fits": [
            "You want more people included: Shop includes 4 people for $69 a month, against 3 on Orderry's Startup.",
            "You'd like a longer trial: 14 days of every Pro feature, against Orderry's 7.",
            "Customers should approve or decline each line of an estimate on their phone and sign, including extra work found mid-repair.",
            "On Pro, Tekly AI drafts estimate lines from the customer's words, and you set the prices.",
        ],
    },
]

GUIDE = {
    "slug": "guides/choose-repair-shop-software",
    "title": "How to Choose Repair Shop Software: A 2026 Checklist | Tekly",
    "desc": "A practical checklist for choosing repair shop software: intake fields, estimate approvals, parts, POS, payroll, pricing traps, data export and switching.",
    "h1": "How to choose repair shop software",
    "lead": "Repair shop software runs the whole job: check-in, the estimate, the customer's approval, parts, payment and the technician's pay. Here's what to check before you pick one — whichever vendor you choose.",
    "sections": [
        ("Start with your intake", "The software should ask for what your trade needs the moment a job comes in: the IMEI or serial for phones and computers, the VIN or plate and mileage for vehicles, engine hours for small engines, the hull ID and each engine's serial for boats. Ask to see a real check-in screen for your trade, not a generic ticket.", None),
        ("Check how customers approve work", "Approval is where repairs stall and where disputes start. Look for approval on the customer's phone, a signature, and the ability to approve some lines and decline others. Ask what happens when you find more work mid-repair: does the customer get a clear choice, and is declined work kept for next time?", None),
        ("Follow a part from order to job", "Order a part from a job, receive it, and see where its cost ends up. Good software charges the part to the right job automatically, so the job's margin is right without anyone re-typing it. If you sell retail too, check the POS and stock work from the same inventory.", None),
        ("Look at money and people together", "Payroll, technician commission, the cash drawer and accounting sync are often separate tools. If they're in one system, commissions come straight from closed jobs and refunds can claw them back. Check whether it syncs with QuickBooks or Xero if your accountant uses them.", None),
        ("Read the pricing page carefully", "Compare the total for your team, not the headline price. Check: how many people are included and what each extra person costs; what each extra location costs; whether prices are monthly or only quarterly or annual; setup or data-migration fees; and which features are paid add-ons. A free trial you can use without talking to sales is a good sign.", None),
        ("Make sure your data can leave", "You should be able to export customers, jobs, stock and finances to CSV or Excel at any time, including after you cancel. Ask the vendor to show you the export.", None),
        ("Plan the switch", "Ask who moves your data and what it costs. Many vendors import an export from your current system or spreadsheets; some charge for it. A single-location shop should expect to be running within a day or two.", None),
        ("Be clear about AI", "AI can draft estimates, summarise a job or suggest a reply. Check that a person always decides what's sent to a customer, that your data isn't pooled with other shops, and that every AI step has a manual path.", None),
    ],
    "faq": [
        ("What is repair shop software?", "One system for everything a repair shop does: checking jobs in, estimates and customer approvals, ordering parts, the point of sale, invoices, inventory and payroll."),
        ("How much does repair shop software cost?", "Published prices range widely. As of October 2026, Tekly starts at $29 a month, Orderry at $39, RepairShopr at $59.99–$69.99, RepairDesk from $79 (billed annually), Tekmetric at $179–$199 per shop and Shopmonkey at $215–$239. Compare what your whole team would pay, including extra people and locations."),
        ("Do I need special hardware?", "Usually not. Most current systems, Tekly included, run in a web browser on a computer, tablet or phone and work with standard barcode scanners, label printers and receipt printers."),
        ("How long does it take to switch?", "For a single-location shop, typically a day or two once your data is exported. Ask whether the vendor moves the data for you and whether they charge for it."),
    ],
}
