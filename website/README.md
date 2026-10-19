# Tekly marketing site

Static site — no build step. Preview locally:

```bash
python3 -m http.server 4321 --directory website
```

## What to edit where

| Change | File |
|---|---|
| Prices ($49 Starter / $99 Pro), plan features, yearly discount, trial length | `site.config.js` → `plans`, `annualDiscount`, `trialDays` |
| App sign-in / sign-up URLs | `site.config.js` → `appUrl`, `signInPath`, `signUpPath` |
| Where demo requests go | `site.config.js` → `demoEndpoint` (POST JSON) or `demoEmail` (mailto fallback) |
| Copy and sections | `index.html` |

## Design system
Follows `algolia.design.md`: a near-black hero washed with a violet-to-cobalt gradient and a
stacked three-word Sora headline ("Diagnose. Approve. Get paid."), then a white editorial body
with hairline (#e5e7eb) cards at 12px corners, and a deep-navy (#000033) product band ending in
large Sora 900 proof numerals (honest product facts, no invented stats). Two typefaces only:
Sora for display, headings and numbers; Inter for everything you read. Electric blue #003dff is
held for pill CTAs and inline links; pills are used for buttons only.

No animation libraries: reveal-on-scroll and the demos are plain JS with IntersectionObserver.
Interactive pieces: the complaint → AI estimate card in the hero, the five job-stage tabs in the
navy band, the phone approval demo, the pricing toggle and the demo / sign-up / sign-in panels.

Measured (headless Chrome): scroll 60 fps, no horizontal overflow from 320px to 1440px;
Lighthouse performance 94 mobile / 98 desktop, accessibility, best practices and SEO 100.

## Visuals
No photography. Every visual is a product screen built in HTML/CSS with sample data (labelled as
such): the AI estimate card, the five job-stage previews, the phone approval demo, a purchase
order, a pay run with revenue chart, and the customer's tracking page. Trades are shown as the
four real templates — Electronics (IMEI or serial, with a Phone/Computer toggle), Vehicles
(cars, trucks, RVs, bikes), Tools & small engine, and Marine — with line icons and example intake.

Trust content is factual: a "works with" strip lists integrations that exist in the codebase
(Stripe, Square, QuickBooks, Xero, SMS via Twilio, WhatsApp, Gmail, Outlook) and a promises row
(no contracts, export anytime, free migration, AI never sends on its own).
