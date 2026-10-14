# RepairOS Finance — gap analysis & how we beat Orderry

*Research + product brainstorm. Target market: SMB repair shops across **US, LATAM, Europe, and Asia** — multi-currency, multi-tax-regime. Pre-launch, multi-tenant B2B SaaS.*

---

## 1. What RepairOS Finance is today (verified in code)

**Finance hub** — Overview KPIs · Transactions · Invoices (create with service/product/labor/custom lines, discount %, tax %, multi-order fan-out, send by email, pay, **UBL 2.1 e-invoice** issue/clear/XML) · Client balances · Payroll (calculation per month, adjustments, runs, payslip print).

**Cash hub** — multiple cash/bank accounts · total money · cash-flow **categories** with Income/Expense direction · transactions with **correction / delete / restore** · **transfers** between accounts · order **pay / prepay / refund** · balances · summary.

**Foundations** — double-entry **ledger** auto-posting on invoice/payment events · **tax groups + tax rules** · **legal entities** · document **numbering** · payment gateways (Stripe/Square/SumUp, now per-shop keyed) · QuickBooks/Xero sync · finance **reports** (cash flow, total money, WO profit, payroll summary).

This is already a serious, real finance module — not a toy. The gaps below are about depth, compliance, and owner-facing insight, not basic plumbing.

---

## 2. What Orderry has that we're thin on (parity gaps)

| # | Orderry capability | RepairOS today | Gap |
|---|---|---|---|
| P1 | **Cash flow statement** for any period, print/export, first-class | `cash_flow` report exists | Medium — needs a proper period statement view + export |
| P2 | **Retroactive/backdated** cash entries (income/transfer/expense) | transactions are "now" | Medium — allow a back-dated `paid_at` with an audit flag |
| P3 | **Fiscal check / receipt reprint** | invoice print only | Low-Med — a payment-receipt document + reprint |
| P4 | **Payroll accruals** (accrue → payout as two steps) + payout category | calc + adjustments + runs | Medium — model the accrual→payout lifecycle |
| P5 | **Rounding rules** (per document family) | not present | Low |
| P6 | **Ordered tax stack** + inclusive/exclusive + **per-category tax exceptions** | tax groups/rules exist | Medium — depth of the tax engine |
| P7 | **Default cash-flow category per document type** (auto-label payments) | categories exist, manual | Low |
| P8 | Fine-grained **finance view-scoping** (see cost/profit/purchase price; "for any period"; edit-after-close) | partial RBAC | Medium — money-data permission matrix |

None of these are moats — they're table stakes Orderry ships. Worth closing the Medium ones, but they don't *win* the market.

---

## 3. How we beat Orderry — "locally correct everywhere" (the real moat)

Orderry is a competent but **one-size-fits-all global** tool: a single generic tax model, Stripe/Square/SumUp payments, QuickBooks/Xero accounting. It is "good enough" in the US and thin-to-wrong the moment a shop needs a **country-mandated fiscal e-invoice, a local payment rail, or its own accountant's file format.** Across US + LATAM + Europe + Asia those requirements are *not optional* — in much of LATAM and the EU, e-invoicing is legally mandatory and Orderry simply doesn't do it.

So the moat is not any one country feature — it's an **architecture bet: a pluggable RegionPack.** Ship the finance core once; make the *locale-specific* pieces plug-ins so RepairOS is correct out of the box in each market, and adding a country is a config + adapter, not a rewrite.

### The RegionPack abstraction (the product bet)

One interface, many implementations. A shop's country selects a pack; everything downstream adapts:

| Slot | What it plugs in | Examples across regions |
|---|---|---|
| **Tax model** | how tax is computed & shown | US sales tax (state/local, origin/destination) · EU VAT (OSS, reverse charge, VIES validation) · LATAM IVA · Asia GST/VAT |
| **E-invoice adapter** | the government/clearing format | EU **EN 16931 / Peppol** (we're already close) · Italy **SdI/FatturaPA** · France **Factur-X/Chorus/PPF** · Mexico **CFDI (PAC)** · Brazil **NF-e/NFC-e (SEFAZ)** · Chile **DTE** · India **IRN+QR (IRP)** |
| **Payment rails** | how customers actually pay | US/EU cards + **ACH/SEPA** (Stripe/Adyen) · Brazil **Pix** · Mexico **SPEI/Mercado Pago** · India **UPI** · SE-Asia wallets (GrabPay, GCash) — all as a **QR/link on the invoice** |
| **Accounting export** | the file the local accountant wants | US/global **QuickBooks/Xero** · Germany **DATEV** · Brazil **Contabilizei/Bling** · India **Tally/Zoho** |
| **Dunning channel** | reminder medium by market | email/SMS (US/EU) · **WhatsApp** (LATAM/Asia) |
| **Format rules** | numbering, rounding, decimals, date/₹/$/€ | per-country defaults |

We already have three big head-starts: a **UBL 2.1 e-invoice pipeline** (that's the EN 16931/Peppol lineage — Europe is closer than it looks), a **double-entry ledger** (accounting exports are serializers over data we already hold), and **per-shop credentials** (each shop plugs its own PAC/gateway keys). Orderry has none of this pluggability — retrofitting country fiscal engines into a one-size app is exactly the kind of rewrite incumbents avoid.

### Tier A — the moat (region-adaptive; win deals per market)
1. **Mandatory e-invoicing done right, region by region.** Sequence by pain + reach: **EU (Peppol/EN 16931 — extends our UBL)** → **LATAM (Mexico CFDI, Brazil NF-e — legally required, Orderry absent)** → **Asia (India IRP, others)**. *Where it's mandatory, this isn't a feature — it's the difference between legal and not.*
2. **Local payment rails as an invoice QR/link.** Pix, SPEI/Mercado Pago, UPI, SEPA/ACH, cards — the shop's region decides which, printed as a scannable QR on invoice + public-pay page. Orderry's Stripe/Square/SumUp only really serve US/parts of EU.
3. **Accountant-native export per market.** QuickBooks/Xero (have) + **DATEV, Tally, Contabilizei/Bling, Zoho** via the ledger. "Send my accountant their file" in the format they use.

### Tier B — owner-facing insight (region-agnostic; safe near-term core, neither tool does well)
4. **Plain-language P&L / "Did I make money?" dashboard** — revenue − parts COGS (`avg_cost`) − labor − expenses, monthly, in the shop's own currency. Orderry shows cash flow, not a true owner P&L.
5. **Cash-flow forecast / runway** — 30/60/90-day projection from receivables aging + PO payables + recurring expenses. Predictive; a genuine gap in both.
6. **Receivables aging + automated dunning** — aging buckets with one-tap reminders on the **region's channel** (WhatsApp in LATAM/Asia, email/SMS in US/EU), carrying the local pay link.
7. **Owner daily money digest** — yesterday's collections, cash on hand, overdue, today's payouts; rides scheduled-tasks + notifications.

### Tier C — modern, mobile-first delight (region-agnostic)
8. **Snap-a-bill expense capture (receipt OCR)** → vendor/amount/date/tax → staged expense.
9. **Bank reconciliation** — import statement (CSV/OFX), auto-match, flag unmatched. Owners live here; Orderry doesn't offer it.
10. **Technician effective-rate & job-level margin in Finance** — extend job-profitability into the finance surface (labor cost vs billed, effective rate/hour).

> Compliance dates and schema versions move constantly (EU ViDA phase-in, per-country mandate deadlines, CFDI/NF-e version bumps). Treat the specifics above as the landscape; **verify the current mandate + schema version for each market at build time** before shipping its pack.

---

## 4. Prioritized roadmap (impact × effort)

**Now — region-agnostic core that lifts every market (lowest risk, highest reach):**
- Plain-language P&L / owner dashboard (Bet 4) — mostly FE over existing ledger + reports.
- Receivables aging + automated dunning w/ pay link (Bet 6) — reuses notifications; channel per region.
- Payment link/QR on invoice + public-pay (Bet 2, framework) — start with cards/Stripe (broadest), designed as a pluggable rail so Pix/UPI/SPEI drop in later.

**Next — stand up the RegionPack framework + first packs:**
- Define the RegionPack interface (tax / e-invoice / payments / export / dunning / format).
- **EU pack first** (extends existing UBL → EN 16931/Peppol; VAT/OSS) — biggest reach with least new work.
- **LATAM pack** (Mexico CFDI + Brazil NF-e) — highest "Orderry can't do this" leverage.
- Accountant exports: DATEV / Tally / Zoho over the ledger (Bet 3).
- Owner daily money digest (Bet 7); parity mediums P1/P2/P4.

**Later — depth + additional packs:**
- Asia packs (India IRP, others) · cash-flow forecast (Bet 5) · receipt OCR (Bet 8) · bank reconciliation (Bet 9) · tax-engine depth, rounding, view-scoping (P6/P5/P8) · fiscal receipt reprint (P3).

---

## 5. The one-line thesis

Orderry is a competent *one-size-fits-all* finance module. We win by being **locally correct in every market** — a pluggable RegionPack for tax, e-invoicing, payment rails, and accountant exports, on top of an owner-legible finance core (P&L, forecast, dunning) that works everywhere. Build the framework once; let each market plug in. Our UBL pipeline, double-entry ledger, and per-shop credentials mean we're already partway there — and the pluggable architecture is precisely what a one-size incumbent can't easily copy.
