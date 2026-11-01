# Client tax exemption

Status: draft for review · 2026-10-15 · mockup: see the "Client Tax Exemption" artifact

## Problem

A shop's exempt customers (resellers, schools, non-profits, government, First Nations status-card
holders in Canada) have to be un-taxed by hand on every visit today. RepairOS has no per-client
exemption at all: tax comes from the tax stack plus item/category exceptions
(`services/tax.py resolve_taxes`), and "Tax exempt" exists only as a custom-field preset that
nothing reads (`routers/forms.py`). Staff forget, customers get overcharged, and when tax is
removed by hand there is no record of why, which is what an auditor asks for.

## Goal

Mark a client exempt once, at the moment it comes up (usually at checkout), and every later
work order, estimate, sale and invoice for that client is taxed correctly with no extra clicks,
with the reason and certificate on record.

Success: an exempt repeat customer needs zero tax edits on their second visit; every untaxed
document can show why (reason + certificate) in one click.

## Competitors

| Product | What it does | Gap we close |
|---|---|---|
| Tekmetric | "Tax exempt" box on customer profile, carries to every RO; one-off per-RO toggle; tax ID field | No reason, no expiry, all-or-nothing |
| ServiceTitan | Customer record set to Non-taxable, propagates to the customer's locations | Needs support to enable; no certificate tracking |
| RepairShopr | "Tax Free" checkbox on customer | Same |
| QuickBooks Online | Exempt toggle + "Reason for exemption" dropdown | No expiry, no certificate file |
| Square | No customer-based exemption (open request since 2016) | Workarounds only |
| Shopify | Per-tax exemption codes (CA status card = provincial part only; BC/MB/SK reseller = PST only) | — we copy this model |

## Decisions

1. **Exemption is a client status, not a tag.** A tag has no reason, no certificate and no audit
   trail. A system "Tax exempt" chip may be shown with the other tags, but it opens this record.
2. **Per tax, not all-or-nothing.** An exemption stores the rate to charge for each tax it touches
   (0 = fully exempt). Presets by reason and region fill it in, so the owner rarely edits it:
   - US resale / non-profit / government: every sales tax → 0.
   - CA status card: provincial portion only (ON HST 13 → 5, BC PST → 0, GST unchanged).
   - CA BC/MB/SK reseller: PST/RST → 0, GST unchanged.
3. **Captured at checkout, remembered by default.** The tax line on a work order / POS sale has
   "Customer is tax-exempt?". The sheet has "Remember for this client's future visits" switched on.
   Switching it off makes a one-off exemption for this document only (still needs a reason).
4. **Visible, never silent.** Exempt documents show a badge in the header and the tax line reads
   "exempt" with the amount not charged, instead of the line disappearing.
5. **Documents freeze what they used.** Each document stores a snapshot of the exemption applied.
   Editing or removing the client's exemption later never changes issued invoices.
6. **Expiry is optional but enforced.** If an expiry date is set: a warning from 30 days before;
   after it passes the exemption stops applying, and new documents show "Certificate expired —
   taxed normally · Update certificate".

## Data model

New table `client_tax_exemptions` (history kept; at most one active per client):

| column | type | notes |
|---|---|---|
| id, org_id, client_id | str | FK clients, organisations |
| reason | str | `resale` `nonprofit` `government` `status_card` `other` |
| certificate_no | str null | required for resale, government, status_card |
| jurisdiction | str null | state/province code the certificate is for, e.g. `NY`, `ON` |
| tax_rates | JSON | `{tax_id: rate_to_charge}`; 0 = exempt; taxes not listed charge normally |
| valid_from | date | defaults to today |
| expires_on | date null | |
| file_id | str null | uploaded certificate (existing attachments) |
| note | text null | required when reason = `other` |
| created_by, created_at, revoked_by, revoked_at | | revoke = remove; row kept |

On `orders`, `sales`, `invoices`: `tax_exemption` JSON snapshot
`{exemption_id, reason, certificate_no, tax_rates, expires_on, one_off}` and
`tax_override` JSON `{charge_tax: true, reason, by, at}` (null when not overridden).

## Tax resolution (per line, per tax)

1. Item/category exception (`TaxException`) → exempt. (existing)
2. Document has `tax_override.charge_tax` → charge normally.
3. Document has a `tax_exemption` snapshot → charge `tax_rates[tax_id]` if listed.
4. Otherwise charge the tax's rate. (existing)

The snapshot is taken when the client is set on the document (and refreshed while the document
is a draft/open and not invoiced). Changing the client re-evaluates. An exemption is active when
`valid_from <= document date <= expires_on` and it is not revoked.

Depends on the location-aware `resolve_taxes` fix and POS sales tax (separate session:
"Fix tax and numbering engine gaps before launch").

## Surfaces

1. **Work order / POS checkout** — tax line link "Customer is tax-exempt?" → sheet: reason chips
   (by region), certificate #, state/province, expiry, upload photo, "Remember for future visits"
   (on), live preview of the new total → "Apply exemption". Afterwards: badge in the header,
   tax line "Sales tax 8.875% — exempt · $25.38 not charged", menu: "Charge tax on this order".
2. **Client profile** — "Tax status" row: `Taxable · Mark tax-exempt`, or a card: reason,
   certificate, jurisdiction, what it covers, expiry, View certificate / Edit / Remove, history.
   When marked exempt with open, un-invoiced documents: "Also apply to 2 open work orders?" (yes).
3. **Next visit** — choosing the client on a new WO shows the badge immediately and taxes
   accordingly. Expiring soon: amber badge "expires in 12 days". Expired: red banner, taxed.
4. **Invoice / receipt print** — line "Tax exempt: Resale, certificate ST-120 #88341".
5. **Reports** — "Exempt sales": date, document, client, reason, certificate, amount not charged
   per tax. Filter by period and jurisdiction.

## Permissions & audit

- New code `can_manage_tax_exemptions` (catalog group Finance; denied in the Specialist template).
  Needed to create/edit/remove an exemption, including one-off.
- "Charge tax on this order" only adds tax → allowed for everyone, reason required.
- Every create/edit/remove/override writes an audit log entry.

## Out of scope (v1)

Automatic certificate validation, multi-state certificate sets, tax-service integrations
(Avalara/TaxJar), exemption by client group, customer self-upload from the portal.

## Open questions

1. Is a certificate number mandatory for US resale, or only "strongly suggested"?
2. Should an expired certificate block checkout, or just tax normally (proposed: tax normally)?
3. HST status-card relief applies off-reserve only in Ontario; do we need a delivery-location
   check, or trust staff?
4. Do pilot shops have enough exempt customers to justify expiry reminders in v1?
