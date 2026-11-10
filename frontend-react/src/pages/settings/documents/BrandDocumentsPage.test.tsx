import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within, act } from "@testing-library/react";
import { SEEDED_TEMPLATES } from "../templateEditor/seededTemplates.fixture";

const get = vi.fn(); const post = vi.fn(); const put = vi.fn();
vi.mock("../../../api/client", () => ({ api: { get: (...a: any[]) => get(...a), post: (...a: any[]) => post(...a), put: (...a: any[]) => put(...a), del: vi.fn() } }));

import BrandDocumentsPage, { AUTOSAVE_MS, BRAND_SWATCHES } from "./BrandDocumentsPage";
import { pageBox, withStyle, metaOf } from "./shopDocuments";

const row = (id: string, name: string, doc_type: string, body: string, extra: Record<string, unknown> = {}) => ({
  id, name, doc_type, body_html: body, page_size: doc_type === "receipt" ? "receipt80" : doc_type === "label" ? "label" : "a4", font_family: "Inter",
  is_default: true, auto_print_on: "", page_w_mm: doc_type === "label" ? 40 : null, page_h_mm: doc_type === "label" ? 30 : null, margin_mm: null,
  barcode_field: null, barcode_types: {}, ...extra,
});
const TEMPLATES = [
  row("i1", "Invoice", "invoice", SEEDED_TEMPLATES["Preset: Detailed invoice"]),
  row("r1", "Sales receipt", "receipt", SEEDED_TEMPLATES["Sales receipt"]),
  row("e1", "Estimate", "estimate", SEEDED_TEMPLATES["Preset: Standard estimate"]),
  row("w1", "Work order", "work_order", SEEDED_TEMPLATES["Preset: Full work order"]),
  row("l1", "Price tag", "label", SEEDED_TEMPLATES["Price tag"]),
];
let brandColor: string | null = null;
const previews: { doc_type: string; brand: string | null }[] = [];

beforeEach(() => {
  get.mockReset(); post.mockReset(); put.mockReset(); previews.length = 0; brandColor = null;
  get.mockImplementation(async (p: string) => (p === "/branding" ? { primary_color: brandColor } : p === "/staff/company" ? { logo_data: "", company_note: "90 days" }
    : { items: TEMPLATES, variable_groups: [{ group: "Legal entity", items: ["{Legal Entity Tax ID}", "{Legal Entity IBAN}"] }] }));
  post.mockImplementation(async (p: string, b: any) => {
    if (p === "/documents/preview") { previews.push({ doc_type: b.doc_type, brand: brandColor }); return { html: (brandColor ? `<style>.tk-branded h1{color:${brandColor}}</style>` : "") + b.body_html, sample: "WO-1001" }; }
    if (p === "/documents/templates") return { id: "new" };
    return { body_html: "<p>{Client Name}</p>" };
  });
  put.mockImplementation(async (p: string, b: any) => { if (p === "/branding") brandColor = b.primary_color; return {}; });
});

const page = () => render(<BrandDocumentsPage templates={TEMPLATES} reload={() => {}} onOpenEditor={vi.fn()} onAllTemplates={vi.fn()} />);
const wait = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));
const chip = (name: string) => within(screen.getByRole("group", { name: "Document" })).getByRole("button", { name });
const templatePuts = () => put.mock.calls.filter(([p]) => String(p).startsWith("/documents/templates/"));

describe("Brand & details", () => {
  it("a brand colour change reaches every document's preview", async () => {
    page();
    await waitFor(() => expect(previews.length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: `Brand colour ${BRAND_SWATCHES[1]}` }));
    await waitFor(() => expect(put).toHaveBeenCalledWith("/branding", { primary_color: BRAND_SWATCHES[1] }));
    for (const name of ["Invoice", "Receipt", "Quote", "Work order", "Label"]) {
      const before = previews.length;
      fireEvent.click(chip(name));
      if (name === "Invoice") await wait(300);
      await waitFor(() => expect(previews.length).toBeGreaterThan(name === "Invoice" ? 0 : before));
      expect(previews.at(-1)!.brand).toBe(BRAND_SWATCHES[1]);
    }
    expect(new Set(previews.filter((x) => x.brand).map((x) => x.doc_type))).toEqual(new Set(["invoice", "receipt", "estimate", "work_order", "label"]));
    expect(templatePuts()).toEqual([]);                                // brand is shop-wide: no template changes
  }, 20000);

  it("a switch adds its section to every document that can show it, and the saved HTML has it", async () => {
    page();
    await screen.findByTestId("feature-technician");
    fireEvent.click(within(screen.getByTestId("feature-technician")).getByRole("switch"));
    await waitFor(() => expect(templatePuts().length).toBeGreaterThan(0), { timeout: AUTOSAVE_MS + 2000 });
    const byId = Object.fromEntries(templatePuts().map(([p, b]) => [String(p).split("/").pop(), b.body_html]));
    expect(byId.i1).toContain('data-tk-section="technician"');
    expect(byId.i1).toContain("{Order Assignee Name}");
    expect(byId.e1).toContain('data-tk-section="technician"');
    expect(byId.r1).toBeUndefined();                                    // receipts can't show it: untouched
    expect(byId.l1).toBeUndefined();
    // and the receipt's switch says why
    fireEvent.click(chip("Receipt"));
    expect(within(screen.getByTestId("feature-technician")).getByRole("switch")).toBeDisabled();
    expect(screen.getByTestId("feature-technician").textContent).toMatch(/Sales don't have a technician/);
  }, 20000);

  it("switching on and back off before the autosave saves nothing: untouched stays byte for byte", async () => {
    page();
    await screen.findByTestId("feature-signature");
    const sw = () => within(screen.getByTestId("feature-signature")).getByRole("switch");
    fireEvent.click(sw()); fireEvent.click(sw());
    await wait(AUTOSAVE_MS + 500);
    expect(templatePuts()).toEqual([]);
  }, 20000);

  it("paper is saved per document", async () => {
    page();
    const paper = await screen.findByRole("group", { name: "Paper" });
    fireEvent.click(within(paper).getByRole("button", { name: "Letter" }));
    await waitFor(() => expect(templatePuts().length).toBe(1), { timeout: AUTOSAVE_MS + 2000 });
    const [p, body] = templatePuts()[0];
    expect(p).toBe("/documents/templates/i1");
    expect(body.page_size).toBe("letter");
    expect(body.body_html).toBe(TEMPLATES[0].body_html);               // the page itself untouched, byte for byte
    expect(Object.keys(body).sort()).toEqual(["auto_print_on", "barcode_field", "barcode_types", "body_html", "doc_type", "font_family",
      "is_default", "margin_mm", "name", "page_h_mm", "page_size", "page_w_mm"]);
    fireEvent.click(chip("Receipt"));
    expect(within(screen.getByRole("group", { name: "Paper" })).getByRole("button", { name: "80 mm" })).toHaveAttribute("aria-pressed", "true");
  }, 20000);

  it("Add detail suggests details for this document and inserts the right field", async () => {
    page();
    fireEvent.click(await screen.findByRole("button", { name: /Add detail/ }));
    const pop = screen.getByRole("dialog", { name: "Add detail" });
    expect(within(pop).getAllByRole("button").length).toBeGreaterThanOrEqual(6);
    expect(within(pop).getByRole("button", { name: /Due date/ })).toBeDisabled();          // the invoice already shows it
    fireEvent.click(within(pop).getByRole("button", { name: /Your tax number/ }));
    // search over all fields
    fireEvent.change(within(pop).getByLabelText("Search all fields"), { target: { value: "iban" } });
    fireEvent.click(await within(pop).findByRole("button", { name: /Legal Entity IBAN/ }));
    await waitFor(() => expect(templatePuts().length).toBe(1), { timeout: AUTOSAVE_MS + 2000 });
    const body = templatePuts()[0][1].body_html as string;
    expect(body).toContain('<p data-tk-section="field:{Legal Entity Tax ID}"><strong>Your tax number:</strong> {Legal Entity Tax ID}</p>');
    expect(body).toContain("{Legal Entity IBAN}");
  }, 20000);

  it("Style sets the font for every document", async () => {
    page();
    fireEvent.click(await screen.findByRole("button", { name: /Classic/ }));
    await waitFor(() => expect(templatePuts().length).toBe(5), { timeout: AUTOSAVE_MS + 2000 });
    expect(templatePuts().every(([, b]) => b.font_family === "Georgia")).toBe(true);
  }, 20000);

  it("Customise layout opens the full editor on the document shown", async () => {
    const onOpenEditor = vi.fn();
    render(<BrandDocumentsPage templates={TEMPLATES} reload={() => {}} onOpenEditor={onOpenEditor} onAllTemplates={vi.fn()} />);
    await screen.findByRole("group", { name: "Paper" });
    fireEvent.click(screen.getByRole("button", { name: /Customise layout/ }));
    await waitFor(() => expect(onOpenEditor).toHaveBeenCalledWith(expect.objectContaining({ id: "i1", doc_type: "invoice" })));
  }, 20000);
});

describe("shopDocuments", () => {
  it("draws the page at its real width", () => {
    expect(pageBox(metaOf({ page_size: "a4" }, "invoice")).w).toBe(794);
    expect(pageBox(metaOf({ page_size: "receipt80" }, "receipt"))).toEqual({ w: 302, h: null, pad: 0 });
    expect(pageBox(metaOf({ page_size: "label", page_w_mm: 40, page_h_mm: 30 }, "label")).w).toBe(151);
  });
  it("Compact tightens full-page margins only, and leaving it undoes only its own margin", () => {
    const a4 = metaOf({ page_size: "a4", margin_mm: null }, "invoice");
    expect(withStyle(a4, "compact").margin_mm).toBe(6);
    expect(withStyle(withStyle(a4, "compact"), "modern").margin_mm).toBeNull();
    expect(withStyle(metaOf({ page_size: "a4", margin_mm: 15 }, "invoice"), "classic").margin_mm).toBe(15);
    expect(withStyle(metaOf({ page_size: "receipt80", margin_mm: 2 }, "receipt"), "compact").margin_mm).toBe(2);
  });
});
