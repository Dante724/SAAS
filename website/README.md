# Tekly marketing site

Static site — no build step. Preview locally:

```bash
python3 -m http.server 4321 --directory website
```

## What to edit where

| Change | File |
|---|---|
| Prices, plan names/features, yearly discount, trial length | `site.config.js` → `plans`, `annualDiscount`, `trialDays` |
| App sign-in / sign-up URLs | `site.config.js` → `appUrl`, `signInPath`, `signUpPath` |
| Where demo requests go | `site.config.js` → `demoEndpoint` (POST JSON) or `demoEmail` (mailto fallback) |
| Calculator assumptions | `site.config.js` → `calculator` |
| Photos per slot + credits | `photos.js` |
| Copy and sections | `index.html` |

## Design system
Follows `stripe.design.md`: white canvas, animated gradient mesh (`mesh.js`, WebGL with CSS fallback),
indigo `#533afd` as the single filled CTA per band, Hanken Grotesk display/body with tight
tracking, IBM Plex Mono for IDs, tabular figures on every number (Geist only for the wordmark).
Refined with the Impeccable detector: no italic-serif accents, no marquee, no icon tiles, flat
sections instead of nested cards, hero copy on white with the gradient behind the product.
Scroll motion uses GSAP + ScrollTrigger reveals on native scrolling (smooth-scroll and scroll
pinning were removed — they made the page drift and lag). The mesh renders at half resolution,
capped at 30 fps, and pauses off-screen. Everything respects `prefers-reduced-motion` and still
works if the CDNs fail.

Measured (headless Chrome): scroll 60 fps with no long frames; Lighthouse mobile 98
performance, accessibility 97.

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
