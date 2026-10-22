---
name: Tekly
source: frontend/static/css/design.css (single source of truth, light + dark)
colors:
  primary: "#4f8ef7"        # --blue
  primary_dark: "#1d4ed8"   # --blue-dark
  gradient_partner: "#9b7bf4"  # --purple (used sparingly, brand accent only)
  success: "#3dca7a"        # --green
  warning: "#f5a623"        # --amber
  danger: "#f2645a"         # --red
  teal: "#2cd4c0"           # --teal
  text: ["#14213f", "#435577", "#6c7a99", "#9aa7c4"]   # --tx-1..4
  glass_fill: "rgba(255,255,255,.72)"     # --g-fill
  glass_border: "rgba(255,255,255,.92)"   # --g-border
typography:
  family: system-ui / Inter stack (inherited from design.css body)
  weights: [600, 700, 800]
  scale: { title: 30, h: 18, body: 14, label: 11, micro: 9.5 }
  label_style: uppercase, letter-spacing .07em, color --tx-3   # section micro-labels
rounded: { xs: 8, sm: 12, md: 16, lg: 22, xl: 28, pill: 999 }   # --r-*
elevation:
  clay_sm: "--clay-sm"   # soft neumorphic card
  clay_md: "--clay-md"
  glass_blur: "blur(28px) saturate(180%)"   # --g-blur
status_tints:   # soft-tint pills — bg = colour @ ~12%, text = darker shade, dot = solid colour
  new: "#4f8ef7"
  in_progress: "#9b7bf4"
  pending: "#f5a623"
  ready: "#2cd4c0"
  done: "#3dca7a"
  dropped: "#9aa7c4"
components: [card (glass/clay), soft-tint status pill + dot, connected stepper,
  micro-label section header, detail-blob (label/value), timeline, sticky action bar,
  primary/success/glass buttons, signature sheet]
dark_mode: true   # design.css ships a full [data-theme=dark] token set; page must inherit it
---

# Tekly — public page design system

Extracted from the live app's token file (`frontend/static/css/design.css`), which both the
staff app and the public pages already import. This DESIGN.md is the contract for building any
public-facing page (track / estimate / e-sign / pay) so it reads as the same product.

## Principles
- **Brand skin, not staff chrome.** Public pages carry the tokens (glass/clay cards, tint
  pills, stepper, type scale) on a calm single-column, mobile-first layout — no sidebar/tables.
- **Action-first.** If the customer has something to do (sign an estimate, approve a WO, pay a
  balance) the page leads with that action + a sticky CTA; otherwise it leads with live status.
- **Soft-tint status, not loud gradient.** Status uses the app's tint-pill (colour dot + ~12%
  fill + darker text), keyed by status group — never the saturated blue→purple gradient block.

## Colors
Use the CSS variables directly (`var(--blue)`, `var(--tx-1)`, …). Never hardcode hex. The token
file flips all of them for dark mode, so the page must not override colours with literals.

## Layout
560px max single column, 18–28px padding, cards `--r-lg`, `--clay-sm`. Sticky bottom action bar
on mobile for the primary CTA.

## Do / Don't
- Do reuse `design.css` classes (`card`, `btn`, `btn-success`, `field-input`, `rec-row`, …).
- Do pull the shop logo + accent from org settings into the header.
- Don't invent new colours, shadows, or radii — everything comes from the token file.
- Don't replicate the staff app's dense chrome; keep one clear action per screen.
