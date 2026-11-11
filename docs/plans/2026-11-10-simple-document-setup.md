# Simple document setup (Settings › Documents) — plan

Goal: the owner sets up every printed document from one screen, "Brand & details", with the
shop's real documents previewed live beside the settings. The full Design editor (#176) stays
one click away ("Customise layout").

Ground rules: same stored HTML and `{Field}` tokens; untouched templates stay byte-identical;
`sanitizeHtml` on save; no API or stored-format change except where noted; grey out, don't hide;
main action first/left; light + dark; no vendor branding; autosave like the editor (≈2 s, only
when changed).

## What already exists (reused, no new storage)
| Need | Existing |
|---|---|
| Logo | `organisations.logo_data`, `PUT /api/staff/company {logo_data}` (partial update) |
| Brand colour | `organisations.brand_primary_color`, `GET/PUT /api/branding {primary_color}`, `{Brand Color}` variable |
| Terms / footer text | `organisations.company_note`, `PUT /api/staff/company {company_note}`, `{Company Note}` variable |
| Style (font set) | per-template `font_family`, `margin_mm` |
| Paper per document | per-template `page_size`, `page_w_mm`, `page_h_mm` |
| Real data | `POST /documents/preview` (+ the probe from #176) |
| Print copies | **no per-template field** → shown greyed with a reason (no API change) |

## Backend (smallest change, no migration)
1. `_render` injects a scoped brand stylesheet when the shop has set a brand colour (headings,
   table header rule, dividers), so preview and print match. Colour validated as `#hex`.
   Resolvers for jobs, invoices, sales and credit notes pass the colour. Labels unchanged.
2. `_resolve_sale` adds `{Company Note}` (terms on receipts).
3. `/documents/preview` for `receipt` previews the newest **sale** (falls back to the newest job),
   matching how receipts print.
Tests: `backend/tests/test_document_brand.py` (brand css present only when set, invalid colour
ignored, tenant-scoped — another shop's colour never leaks; receipt preview uses a sale; company
note on receipts).

## Frontend tasks (each: failing test → code → pass)
1. `templateEditor/simpleDoc.ts` — parse/serialize a template with conditions protected; the
   "content root"; list sections; mark sections/text for the preview; hide/show, move up/down,
   bigger/smaller, set text; add/remove a section. Test: `simpleDoc.test.ts` (round-trips,
   untouched = byte-identical, receipt row conditions survive an edit).
2. `templateEditor/docFeatures.ts` — the "Show on documents" switches and "Add detail"
   suggestions per document type, with availability + one-line reason. Test: switches add/remove
   the right sections and saved HTML; add-detail inserts the right token.
3. `settings/documents/useShopDocuments.ts` — the five chip documents (Invoice, Receipt, Quote,
   Work order, Label): the shop's default template per type, or the standard layout (created on
   first change), autosave per document (≈2 s), paper per document. Test: paper saved per
   document; untouched never saved; first change creates through the existing API.
4. `settings/documents/DocPreview.tsx` — real-data preview in a script-free frame at the real
   paper width; click a section → popover (Hide/Show, Move up, Move down, Bigger, Smaller); click
   plain text → type inline. Test: popover actions round-trip into the template.
5. `settings/documents/BrandDocumentsPage.tsx` — left column (Logo, Brand colour, Style, Show on
   documents, Terms), right column (chips, paper, copies greyed, preview, + Add detail,
   Customise layout). Brand change → every preview reloads. Test: brand change reflected in all
   previews; style writes font to every document.
6. `SettingsHub.tsx` — Settings › Documents opens Brand & details; "All templates" keeps the old
   gallery; "Customise layout" opens `TemplateEditorDrawer`.
7. Checks: full vitest, tsc, build, lint:uitext, lint:design, backend pytest for documents.
8. Screenshots (`screenshots/simple/`), draft PR, self-review against this plan.

Not in scope: country picker; tax title/number/lines and Labour/Labor stay automatic.
