# RepairOS — React redesign parity map (vs previous build)

Legend: ✅ done · ◑ partial (present but shallow / read-only / missing sub-features) · ❌ missing

## Core modules
| Legacy module | React location | Status | Gaps to finish |
|---|---|---|---|
| dashboard | My company → Dashboard | ✅ | — |
| timesheets | My company → Employee hours | ✅ | edit/adjust entries |
| services | My company + Inventory → Services | ✅ | taxes, pricing types |
| products | My company + Inventory → Products/Stock | ✅ | import/export, bulk, labels |
| bundles | My company + Inventory → Bundles | ✅ | — |
| orders | Work orders (hub + drawer) | ✅ | deep drawer parity (below) |
| leads | Inquiries | ✅ | — |
| chats | Chats | ✅ | — |
| bookings | Bookings | ✅ | — |
| tasks | Tasks | ✅ | — |
| sales | Sales (Quick POS) | ✅ | — |
| clients | Contacts → Clients | ◑ | import/export, merge, gift cards, message templates |
| suppliers | Contacts → Suppliers | ✅ | supplier ledger view |
| assets | Assets / Devices | ✅ | — |
| cash | Cash | ✅ | — |
| finance/other | Finance (overview/txns/invoices/balances) | ✅ | — |
| reports | Reports (catalog runner + dashboard) | ✅ | — |

## Inventory deep (tabs present, bodies being built)
| Legacy | React | Status |
|---|---|---|
| inventory (stock) | Inventory → Stock | ✅ |
| purchase orders | Inventory → Purchase orders | ✅ |
| conversions | Inventory → Conversions | ❌ (label only) |
| transfers | Inventory → Transfers | ❌ (label only) |
| stocktake | Inventory → Stock takes | ❌ (label only) |
| returns | Inventory → Returns | ❌ (label only) |
| movements | Inventory → Postings | ❌ (label only) |
| reservations | Inventory → Reservations | ❌ (label only) |
| write-offs | Inventory → Write-offs | ❌ (label only) |
| bins | (none) | ❌ |

## Finance / payroll / staff
| Legacy | React | Status |
|---|---|---|
| payrollcalc | Finance → Payroll | ◑ (calc preview; runs/accrue/adjustment/print pending) |
| payrollsettings | (none) | ❌ (payroll rules editor) |
| staff | Settings → Team & roles | ◑ (read; RBAC roles editor, invites, employee edit pending) |
| settings_finance | Settings → Finance & numbering | ◑ (numbering read; tax rules, legal entities pending) |

## Settings sections
| Legacy | React | Status |
|---|---|---|
| statuses | Settings → Statuses | ◑ (read; reorder + transitions pending) |
| warehouses | Settings → Warehouses | ◑ (read) |
| automations | Settings → Automation | ◑ (read; rule editor pending) |
| notifications | Settings → Notifications | ◑ (read; template/rule editor pending) |
| document templates | Settings → Document templates | ◑ (read; editor pending) |
| labels | (under templates) | ❌ |
| forms | (none) | ❌ (custom-field / forms editor) |
| pricing | (none) | ❌ (pricing types & defaults) |
| directories | (none) | ❌ |
| industry | (none) | ❌ (asset brands / models / groups) |
| settings_integrations | (none) | ❌ (API keys, email, QuickBooks, Twilio) |
| publicpages | (none) | ❌ (public booking page config) |
| resources / locations | Settings (partial) | ◑ |

## Reports family
| Legacy | React | Status |
|---|---|---|
| reports (catalog) | Reports | ✅ |
| profitability | (via catalog?) | ◑ (dedicated job-profitability view pending) |
| insights | (none) | ❌ (insight cards + trends builder) |
| feedback | (none) | ❌ |

## Detail-drawer depth to confirm per module
Work order drawer (checklists, recommendations, invoices/payments, activity), client drawer
(balance, loyalty, purchases, communication), asset drawer, booking detail, task detail,
inquiry detail, chat composer — verify each matches legacy actions end to end.
