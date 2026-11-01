# Sprint plan: close the Orderry June–July gaps (24 Oct 2026)

**Sprint goal:** a shop can enter IMEIs when stock arrives, see its true total profit, and never lose an inquiry, whether it comes from a missed call, the phone app or the API. Every one of Orderry's June–July features is fully in Tekly, plus the improvements below.

**Base:** `roadmap/orderry-parity` (PR #13), the code the gap analysis checked. Each workstream gets its own branch and PR into it.

**Source:** `orderry-jun-jul-2026-gap.md` (23 features: 0 full, 13 partial, 10 missing).

## Team and capacity
Three build sessions run in parallel, one per workstream. Each has its own branch and works on files the others don't touch, and each runs the same process: council → TDD → security and tester review → browser check → PR. The owner reviews the PRs.

| Workstream | Session | Size | Load |
|---|---|---|---|
| A. Stock intake and catalog | 1 | about 5 days | 80% |
| B. Profit and reports | 1 | about 4 days | 75% |
| C. Inquiries everywhere | 1 | about 5 days | 80% |

Planned to roughly 80%, which leaves room for review fixes and merge conflicts.

## Backlog

### A. Stock intake and catalog (inventory, postings, purchase orders, assets)
| P | Item | Gap # | Size | Beyond Orderry |
|---|---|---|---|---|
| P0 | **Bulk IMEI/serial entry when stock arrives.** Scan or paste many IMEIs in manual postings and PO receiving, one row per unit. | 16b | S–M | Duplicate and Luhn checks on IMEIs; scanning one after another; flag an IMEI already in stock or already sold |
| P0 | **Active / written-off filter on assets** | 19 | S | Remembers your choice; shows a count of each |
| P1 | **Product type on posting import** (serialized, expiry-tracked) | 16a | S | The import preview shows which rows still need serials |
| P1 | **Import products into a purchase order**, reusing the posting paste/map pipeline | 13b | S–M | Matches to existing catalog items by SKU or barcode and offers to create the missing ones |
| P1 | **Custom product line with "Save to catalog"** | 3 (partial) | S–M | — |
| P2 | **Search by license plate, IMEI or serial** in asset search and at intake | 21 | S–M | One search box finds a device by any identifier |

### B. Profit and reports (reports, dropped jobs, invoice templates)
| P | Item | Gap # | Size | Beyond Orderry |
|---|---|---|---|---|
| P0 | **Total profit report:** jobs + dropped jobs + counter sales, net of commissions and tax, by period, location and technician | 1 | S–M | Shows the trend against last period, and opens the jobs behind each number |
| P0 | **Dropped jobs:** write off the balance, skip the close checks, keep them out of profit | 5 (partial) | M | The reason is required and reported |
| P1 | **Profit of unsuccessful jobs** (upgrade) | 2 (partial) | S | — |
| P1 | **Report filters:** with or without stock movement; hide zero amounts | 6 | S | Filters stay put between visits |
| P1 | **Job fields on invoices:** job number, fault, technician notes, job custom fields; per-row job data on grouped invoices | 7 (partial) | S–M | — |
| P2 | **Continue as a linked job** | 4 (partial) | S–M | — |

### C. Inquiries everywhere (leads, telephony, Tekly Go, public API, contacts)
| P | Item | Gap # | Size | Beyond Orderry |
|---|---|---|---|---|
| P0 | **A missed call creates an inquiry** (Twilio now) and alerts whoever is on duty | 11 | S | Repeat callers merge into one inquiry, the contact is matched automatically, and a callback task is created |
| P0 | **Richer inquiries on the web:** type, device and serial, custom fields, activity | 8 (web) | M | — |
| P1 | **Inquiries in Tekly Go:** list, create, convert to a job | 8 (Go) | M | Creates an inquiry offline and syncs it later |
| P1 | **API v2:** inquiries, tags, services, bundles; products writable; webhooks match the docs (fixes the dead events) | 9, 10 | M | Tag and bundle webhooks |
| P1 | **Half-built settings switched on:** default comment and file visibility, public employee name, auto-print asset label on create | 17, 18, 20 | S | — |
| P2 | **Address split into fields** (street, city, region, postcode, country) on contacts, keeping the old single line | 12 | M | Postcode format checked per country |
| P2 | **Aircall adapter** behind a switch, tested with signed sample webhooks; goes live once you have an Aircall account | 11 | M | — |

**Not in this sprint** (from the "later" list): per-warehouse replenishment (#14), expired write-off action (#13a), transfer custom statuses (#15), and PO tax and payments (#13c). Ask for them separately.

## Dependencies
- **B's report and dropped jobs:** the report counts dropped jobs, so B builds dropped jobs first.
- **C's Tekly Go screen:** needs the Go PWA stack (PR #18). C bases its Go work on that branch, or waits until it's merged.
- **A and C both touch search:** they share no files. A owns assets and inventory search; C owns contacts and leads.
- **Migrations:** each workstream chains its migration after the current head and re-checks the head before its PR. Merge order: A, then B, then C; whoever merges later re-points their migration.

## Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Merge conflicts on shared files (models, main.py router list, migrations) | A blocked PR | Keep the model changes small; rebase onto the base before the PR; the merge order above |
| Profit numbers disagree with existing per-job profitability | Owners lose trust in the report | One shared profit function; a test reconciles the totals with per-job numbers |
| Aircall can't be tested live with no account | Unknown real-world behaviour | Signed sample payloads from Aircall's docs; off by default |
| Tekly Go offline inquiries | Duplicate inquiries after sync | An idempotency key on create |
| Address split breaks old documents and exports | Wrong addresses on invoices | Keep the old single line, fill it from the new fields, and migrate without data loss |

## Definition of done (per item)
- [ ] A council note before building (PM, engineer, tester, security)
- [ ] Tests first; the backend suite, frontend tests, lint and build pass
- [ ] Security and tester review, with their findings fixed
- [ ] Checked in the browser with demo data; light and dark mode where there's UI
- [ ] A PR into `roadmap/orderry-parity` with what changed and what wasn't verified
- [ ] The owner reviews and merges

## Key dates
| Date | Event |
|---|---|
| Sat 24 Oct | Sprint start; three sessions begin |
| Tue 27 Oct | Mid-sprint check: the P0s in review |
| Fri 30 Oct | Sprint end: PRs ready, demo |
