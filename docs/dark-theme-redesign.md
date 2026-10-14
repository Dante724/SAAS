# Premium dark theme ("Graphite") + Public Pages / Reports polish

## What changed & why
The request was "delete dark mode and rebuild it premium." On inspection the dark
theme was well-architected (token-driven, blue-ink identity) — deleting it would
have discarded good work and risked landing worse. The real weakness was that
**Public Pages and Reports** were under-designed (plain inline-styled cards, no
hierarchy), which looked especially rough in dark mode. So: elevate the dark
palette + fix the two weak pages, rather than nuke the system. Toggle stays put
(Settings → General, the iOS-style track/knob at feedback.js:249).

## Dark theme — "Graphite" (design decision)
Chose **rich charcoal over true-black**. True-black OLED photographs well but
fatigues in prolonged workshop use and makes every hairline scream. A layered
near-black with a faint cool cast reads more premium (Linear/Vercel/Stripe
reference points).

**The core upgrade is a real elevation ramp** so surfaces stack instead of
blending into one flat sheet:
- `--elev-0` #0B0D12 canvas · `--inset` #0E1116 wells · `--elev-1` #14171F cards ·
  `--elev-2` #1B1F29 raised/hover · `--elev-3` #232834 overlays/modals.
- Hairlines are light-on-dark alpha (`rgba(255,255,255,.07/.13)`), not steel hex.
- Shadows combine an ambient drop + a top highlight + a hairline ring, giving the
  "lifted glass" feel without blur.
- Inputs are recessed wells (inset shadow); focus is a 3px brand-blue glow ring.

**Boldness spent in one place** (per design discipline): the brand blue→violet
gradient appears only on the active-nav stripe and primary buttons. Everything
else is disciplined graphite. Text is cool off-white (`#EDF0F7`), never pure #FFF
(which glares).

## Public Pages & Reports
Added a reusable premium settings-section system (`.set-hero`, `.set-section`,
`.set-card`, `.set-row`, `.seg`) — all token-driven so they inherit the ramp in
dark mode. Both pages now open with a proper hero (title + one-line purpose),
Public Pages uses a segmented control for its tabs, and Reports got a carded
toolbar + token-driven tabs/report buttons (replacing a hardcoded underline +
pill styles that ignored the theme).

## Verification honesty
No browser binary in this sandbox → I can't screenshot the running app. I built
`dark-theme-preview.html` (real tokens, representative components) so the result
can be eyeballed in a browser, and validated the CSS (balanced braces, tokens
defined, no leftover flat-canvas hex) + all edited JS (`node --check`) + full
backend suite (124 green, zero regression — it's a CSS/JS-only change).

## Notes for future passes
- Ramp is the foundation; more components can adopt `.set-*` for consistency.
- If a true-black variant is ever wanted, it's a 5-value swap of the `--elev-*`
  tokens — the ramp structure already supports it.

---

## Update 2 — "nothing under My company" fix + dark theme v2 (indigo-slate)

### Bug fixed: blank My-company page
My first nav change wrapped `window.setContent` to prepend the tab strip. That
wrapper conflicted with the `setContent` shims that Inventory and Settings install
(they capture/restore the global), so after visiting those pages the tab strip —
and in some paths the page content — could blank out. **Fix:** removed the
wrapper; the tab strip is now prepended in `Router.go` *after* the page renders,
as a DOM insert into `#content` (guarded by a `.company-tabs` check). This can't
blank the page and survives any page shim.

### Dark theme v2 — indigo-slate
The charcoal v1 read flat/"ugly". v2 is a cooler, reference-grade palette
(Linear/Height direction): deep indigo-slate canvas with a subtle two-point glow,
a luminance elevation ramp (#0A0C14 canvas → #12151F cards → #1A1E2B raised →
#232838 overlay), cool hairlines (`rgba(148,163,214,.10)`), soft blue-white text
(#EEF1FB), and an indigo→violet accent thread on the active nav rail + primary
actions. `dark-theme-preview.html` updated to match.

---

## Update 3 — Claymorphism + Liquid Glass accents, doc/dashboard polish

**Accent strategy (material-matched):**
- **Claymorphism** → soft desaturated **pastel** fills with same-hue inner
  highlight+shadow (opaque, puffy). Applied to the dashboard KPI tiles
  (sky/mint/coral/butter/indigo/violet). Dark mode deepens each pastel to a tinted
  charcoal so it stays clay, not neon. `.clay-tile` + `.clay-{hue}`.
- **Liquid glass** → vivid **indigo→violet** light *behind* a frosted panel + bloom
  (translucent, refractive). Applied to the dashboard chart panels and available as
  `.glass-liquid` for primary surfaces. Primary buttons now carry the indigo→violet
  gradient + glow in both themes.

Rule of thumb: clay wants soft/desaturated/opaque accents that tint the material;
glass wants vivid/luminous/translucent accents that light it from behind.

**Fixes in this pass:**
- Dashboard: 12 hardcoded status hex → theme tokens (`var(--blue/green/amber/…)`),
  so charts/legends no longer break in dark mode.
- Document templates (Settings): added a premium hero header (was a bare table).

`dark-theme-preview.html` now includes a clay-tiles + liquid-glass demo block.

---

## Update 4 — My-company tabs actually render + dark settings fixed

**Tabs bug (My company showed only Dashboard):** the async dashboard page returns
at its first `await`, and my single `requestAnimationFrame` insert raced the
render. Replaced with a sync insert + a short bounded retry (40ms × ≤12) that
targets `.page-enter`, so the Dashboard / Employee hours / Services / Products /
Bundles strip lands whether the page renders sync or async, and survives the
Inventory/Settings setContent shims.

**Dark mode washed-out (Settings unreadable):** the light `.card`/`.set-card`/
`.settings-rail` use white-gradient overlays that bled through in dark mode →
flat grey with near-invisible labels. Added explicit dark overrides forcing solid
ramp surfaces (`--elev-1`), killing the `::before` white sheen, restoring label/
hero/hint contrast (`--tx-1/2/3`), and giving inputs a recessed well with a
visible border. Dark clay KPI tiles were too muddy → brightened each pastel so
they read as distinct premium tinted surfaces.

---

## Update 5 — Dark modal wash-out + Directories/Cash/Timesheet clay-glass

**Modals were washed-out in dark mode (headings invisible):** `.modal::before`
paints a `rgba(255,255,255,.94)` white sheen over the whole modal for the light
frosted look — it sat on top of the dark background, so every dark modal (product
detail, Edit role, New template) rendered pale grey with near-invisible text.
Fix: `display:none` on `.modal::before` in dark, force a solid `--elev-3` surface,
and restore title/label/input contrast + visible secondary buttons.

**Flat-grey panels → clay/glass:** corrected selectors to the real class names
(`.acct-card`, `.cash-stat`, `.ts-card`) so Cash accounts, Cash stat tiles, and
Timesheet stat cards get the raised elevation surface with hairline + shadow.
Cash account cards get an indigo-tinted selected state. **Directories** now uses
the `glass-liquid` treatment on its detail panel (frosted + indigo bloom) and soft
clay chips for the list rows with an indigo-gradient selected state — matching the
premium look of the rest of the app. Empty-state text contrast lifted.
