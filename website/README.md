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
| Photos per slot + credits | `photos.js` |
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

## Photography
Two council rounds (art director, shop owner, conversion marketer; majority vote) picked the
photos. Round 2 judged 224 commercially-licensed Wikimedia Commons photos against each current image.

- **Real photos (credited automatically, `*-cc.jpg`):** phone, computer, small engine, auto,
  bike, motorcycle, general repair.
- **Still illustrative (AI-generated):** tools, powersports, marine, fleet, pickup, owner, parts.
  No real candidate was on-topic for these, or the council's pick was vetoed: the fleet pick was
  a government-branded fire truck, the marine picks were US Navy photos (they can read as an
  endorsement) and the parts pick showed walls of third-party brand packaging.

Served images are WebP in `assets/web/` (1400w + 720w). Real photos are reframed and graded to
a house look (credited as adapted); illustrative ones get a daylight grade so the set matches.
To replace a slot, add the file to `assets/photos/` and put its path first in `photos.js`
(add a `credit` entry if the licence requires attribution). Realistic daylight versions of
the remaining slots also sit in the Figma file "Tekly website photography" (frames 15:2–28:2)
if you export them.
