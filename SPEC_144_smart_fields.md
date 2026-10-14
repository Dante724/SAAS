# Spec — Smart Fields: forms that think (beyond Orderry)

Status: Draft · Owner: Chomu · Target build: 144gp+
Scope: make RepairOS custom fields *do work*, not just store values — conditional
visibility, a single field-rule surface shared with the checklist/status engines,
and self-filling fields. Built on the existing `CustomField` + `FormLayout` models
and the WorkOrder forms editor; reuses the recommendation and status-gate engines
shipped in 144go/144gj. No wholesale redesign of the forms editor.

Guiding line: Orderry's fields are inert data-entry boxes. Ours become the shop's
automation surface. We do not win a field-type arms race — we win by making a field
change what the form shows, what the order does, and what the tech doesn't have to type.

---

## Problem statement
Repair intake forms are bloated and dumb. Every field of a work-order type shows on
every order whether relevant or not, so techs scroll past IMEI on a laptop and
"water-damage details" on a dropped phone. The data they *do* enter just sits there —
it can't gate a status, draft an upsell, or save the next tech from retyping the same
device specs. Orderry has exactly this limitation: static per-type fields, no logic,
no reuse. A forms system that reacts is a capability no repair CRM in this tier has.

## Goals
- Cut visible-but-irrelevant fields on a form via one simple condition per field
  (target: measurably fewer fields shown per order, faster intake).
- Let any field value feed the automation the shop already relies on — upsell drafts
  and status gates — from one rules surface, not three bolted-on ones.
- Eliminate repeat data entry: fields that can pre-fill do.
- Stay zero-config: every smart behavior has a sane default and the form works
  untouched if the owner never opens the rule editor.

## Non-goals
- No general workflow/automation builder. Field rules do three concrete things
  (show/hide, draft-upsell, gate-status); they are not a scripting language. Anything
  beyond that routes to the existing AutomationRule engine.
- No nested / multi-condition logic in v1 (one field, one operator, one value). Named
  as a deliberate ceiling — it's where forms products drown.
- No cross-object *computed* fields (formulas) in v1. Auto-fill copies a known value;
  it does not calculate.
- Not a redesign of the forms editor — this extends it. (But note the React forms/
  checklist builder still needs porting; see Dependencies.)
- The four raw parity gaps (multi-select, link type, show-on-print flag, extra
  entities) ride along as P1, not the headline.

## User stories
- As a shop owner, I want a field to appear only when another field has a certain
  value, so intake forms show only what's relevant to the device in front of the tech.
- As a tech, I want irrelevant fields to disappear as I fill the form, so I'm not
  scrolling past IMEI on a laptop repair.
- As a shop owner, I want a numeric or dropdown field to auto-draft an upsell when it
  crosses a threshold — the same way a checklist item does — configured in one place.
- As a shop owner, I want a field value to be able to block a status change (e.g.
  "damage severity = severe" blocks Ready until a manager signs off).
- As a tech, I want device specs and warranty info to pre-fill from the asset / model /
  last order, so I don't retype what the shop already knows.
- As a shop owner, I want a custom field to optionally show on the printout and the
  customer portal, so the customer sees what's relevant.

## Requirements

**P0 — Must have (the two that make us better than Orderry)**

- P0-1 · Conditional visibility. A `CustomField` gains an optional show-rule:
  `show_when_field_key` + `show_when_op` (`is` | `is_not` | `gt` | `lt` | `filled`) +
  `show_when_value`. The form renderer evaluates it live as other fields change; a
  hidden field is not required and its value is not submitted. Migration adds nullable
  columns. Acceptance: given IMEI configured to show when Device type = Phone, the IMEI
  field renders for a Phone order and is absent (and not required) for a Laptop order,
  updating live as the type field changes.
- P0-2 · Unified field-rule surface. Extend the field's config with an optional action
  rule reusing the 144go/144gj primitives: `action` (`draft_upsell` | `gate_status`),
  a trigger (`op` + `threshold`/`value`), and the action payload (for upsell:
  `upsell_ref_id`/`kind`/`price`, drafting through the *same* `Recommendation`
  `source="field"` path; for gate: which status is blocked until the condition clears).
  One rule per field. Acceptance: a "battery %" number field with `draft_upsell` under
  80 produces the identical pending recommendation a checklist item does (same engine,
  same approval, same commission) — verified by a test asserting parity with the
  checklist path.
- P0-3 · One authoring model. The upsell/gate rule editor is the *same component and
  data shape* whether attached to a checklist item or a custom field — so there is one
  place to learn, and no ambiguity about which rule wins. Acceptance: the rule config
  object validates against one schema in both surfaces; a documented precedence when
  both a field and a checklist item target the same status/upsell.
- P0-4 · Zero-config safety. A field with no show-rule always shows; a field with no
  action rule behaves exactly as today. Nothing about existing forms changes until an
  owner opts in. Acceptance: existing orgs' forms render byte-for-byte the same before
  any rule is set.

**P1 — Nice to have**

- P1-1 · Auto-fill. A field can declare a source (`asset.<attr>`, `device_model.<attr>`,
  `last_order.<field_key>`); on form open it pre-fills, editable, badged with its origin
  ("from device model"). Read-only copy, not a formula.
- P1-2 · Parity field types: `multiselect` and `link` (URL) added to `VALID_FIELD_TYPES`
  + renderer.
- P1-3 · `show_on_print` / `client_visible` flag on `CustomField` (mirrors the checklist
  item flag) so a field can surface on documents + the track page.
- P1-4 · Custom-field filters in list views (columns already exist).

**P2 — Future**
- P2-1 · Multi-condition rules (AND/OR) — only if data shows the one-condition ceiling
  actually bites.
- P2-2 · Custom fields on services, suppliers, leads/inquiries.
- P2-3 · Computed/formula fields.

## Success metrics
- Leading (30 days): among orgs that set at least one show-rule, median visible fields
  per order drops vs their pre-rule baseline; no rise in "required field blocked me" errors.
- Leading: field-sourced upsell drafts accepted-or-kept rate ≥ the checklist upsell rate
  (proves the shared engine carries over, not that a new nag was born).
- Guardrail: share of orgs that open the rule editor and successfully save a rule (if
  it's near zero, the editor is too complex — simplify, don't add).
- Lagging (quarter): intake time per order; retention of orgs that adopted smart fields.

## Open questions
- [product] Precedence when a field rule and a checklist item both gate the same status
  or draft the same upsell — dedupe by target, or let both stand? (Lean: dedupe by
  target key, first-configured wins, surface a warning in the editor.)
- [product] Should a hidden (conditional) field retain a previously-entered value if it
  re-appears, or clear it? (Lean: retain — hiding is display, not deletion.)
- [design] One rule editor for show-logic AND action-rules, or two tabs? (Lean: two
  clearly-labelled sections in the field editor — "When to show" and "What it does".)
- [eng] Auto-fill source catalog — which asset/model/last-order attributes are safe and
  cheap to expose without an N+1 on form open?

## Dependencies / sequencing
1. **React forms/checklist builder** — the smart-field config UI lives in the forms
   editor, and the React Settings ▸ Checklists/forms builder still isn't fully ported.
   P0-1/P0-2 need a place to author the rule. Build or extend that first.
2. **Unify with 144go checklist upsell** — P0-2/P0-3 should generalize the existing
   `_sync_checklist_upsell` into a shared `apply_rule(source, ref)` service so field
   rules and checklist rules run the same code. This is a refactor of shipped code —
   do it deliberately, with the 144go tests as the regression net.
3. Close out 144gn/144go on the live instance (migration + server restart) before
   stacking another migration on top.

## Phasing
- **Phase A — Conditional fields (P0-1, P0-4):** the demoable win, self-contained, no
  engine refactor. Ship first.
- **Phase B — Unified rule surface (P0-2, P0-3):** refactor checklist upsell into a
  shared rule service, then let fields use it. Ship with parity tests.
- **Phase C — Auto-fill + parity types (P1):** the daily-feel and gap-closing batch.

Each phase: TDD backend, `tsc`/vite build, gates, changelog, live verify.
