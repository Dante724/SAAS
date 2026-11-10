import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { SEEDED_TEMPLATES } from "./templateEditor/seededTemplates.fixture";

const get = vi.fn();
const post = vi.fn();
const put = vi.fn();
vi.mock("../../api/client", () => ({ api: { get: (...a: any[]) => get(...a), post: (...a: any[]) => post(...a), put: (...a: any[]) => put(...a), del: vi.fn() } }));
// jsdom can't run the GrapesJS canvas frame, so the page builder runs headless here (the same
// engine and parser; the canvas itself is checked in the browser screenshots).
vi.mock("grapesjs", async (orig) => {
  const m: any = await orig();
  const init = (cfg: any) => m.default.init({ ...cfg, headless: true });
  return { ...m, default: { ...m.default, init } };
});
vi.mock("../../lib/dialog", () => ({ confirmDialog: vi.fn(async () => true) }));

import TemplateEditorDrawer, { pickView } from "./TemplateEditorDrawer";

const GROUPS = [{ group: "Client", items: ["{Client Name}", "{Client Phone}"] }];
const tpl = (name: string, extra: Record<string, unknown> = {}) => ({
  id: "t1", name, doc_type: "invoice", body_html: SEEDED_TEMPLATES[name], page_size: "a4", font_family: "Inter", is_default: false,
  auto_print_on: "", page_w_mm: null, page_h_mm: null, margin_mm: null, barcode_field: null, barcode_types: {}, ...extra,
});
// Vendor names and badges that must never reach the screen.
const VENDOR = /powered by|tiptap|grapes|tinymce|prosemirror|made with|\bgjs\b/i;

beforeEach(() => {
  get.mockReset(); post.mockReset(); put.mockReset();
  get.mockImplementation(async (p: string) => (p === "/documents/templates" ? { items: [], variable_groups: GROUPS } : { items: [] }));
  post.mockResolvedValue({ html: "<p>preview</p>" });
  put.mockResolvedValue({});
  localStorage.clear();
});

describe("pickView", () => {
  const ok = { design: null, text: null, html: null };
  it("opens the last view used when it can hold the template, else Design, Text, HTML", () => {
    expect(pickView(ok, "text")).toBe("text");
    expect(pickView(ok, null)).toBe("design");
    expect(pickView({ ...ok, design: "no" }, "design")).toBe("text");
    expect(pickView({ design: "no", text: "no", html: null }, null)).toBe("html");
  });
});

describe("TemplateEditorDrawer", () => {
  it("greys out Design and Text with a one-line reason when they'd change the template", async () => {
    render(<TemplateEditorDrawer initial={tpl("Sales receipt", { doc_type: "receipt" })} onClose={() => {}} />);
    await screen.findByTestId("reason-design");
    expect(screen.getByRole("button", { name: /Design/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Text/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /HTML/ })).toBeEnabled();
    expect(screen.getByTestId("reason-design").textContent).toMatch(/\{#if Total Discount\} between table rows/);
    expect(screen.getByTestId("reason-text").textContent).toMatch(/between table rows/);
    // It falls back to the HTML view, showing the template exactly as stored.
    expect((screen.getByLabelText("HTML source") as HTMLTextAreaElement).value).toBe(SEEDED_TEMPLATES["Sales receipt"]);
  });

  it("opens the Text view with placeholders as chips and no vendor branding", async () => {
    localStorage.setItem("tk_template_view", "text");
    const { container } = render(<TemplateEditorDrawer initial={tpl("Credit note", { doc_type: "credit_note" })} onClose={() => {}} />);
    await waitFor(() => expect(container.querySelector(".tk-text-page")).toBeTruthy(), { timeout: 5000 });
    expect(screen.getByRole("button", { name: /Design/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Text/ })).toBeEnabled();
    const pills = Array.from(container.querySelectorAll(".tk-text-page .tok-pill")).map((e) => e.textContent);
    expect(pills).toContain("Client Name");
    expect(pills).toContain("Credit Note Table");
    expect(container.textContent || "").not.toMatch(VENDOR);
    expect(screen.queryByTestId("reason-text")).toBeNull();
  });

  it("opens the Design view with Tekly's own blocks and no vendor branding", async () => {
    const { container } = render(<TemplateEditorDrawer initial={tpl("Estimate", { doc_type: "estimate" })} onClose={() => {}} />);
    const panel = within(await screen.findByRole("complementary", { name: "Blocks" }, { timeout: 5000 }));
    for (const b of ["Text", "Heading", "Image", "Shop logo", "Divider", "Two columns", "Items table", "Totals", "Signature line"]) {
      expect(panel.getByRole("button", { name: new RegExp(`^${b}$`) })).toBeInTheDocument();
    }
    fireEvent.click(panel.getByRole("button", { name: /Client/ }));                    // open the group
    expect(panel.getByRole("button", { name: "Client Name" })).toBeInTheDocument();      // a block per variable   // a block per variable
    expect(container.textContent || "").not.toMatch(VENDOR);
    expect(container.querySelector(".gjs-pn-panels, .gjs-logo")).toBeNull();          // none of the library's own panels
  });

  it("saves an untouched template with the same payload as before, body byte for byte", async () => {
    localStorage.setItem("tk_template_view", "text");
    const initial = tpl("Estimate", { doc_type: "estimate" });
    const { container } = render(<TemplateEditorDrawer initial={initial} onClose={() => {}} />);
    await waitFor(() => expect(container.querySelector(".tk-text-page")).toBeTruthy(), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    const [path, payload] = put.mock.calls[0];
    expect(path).toBe("/documents/templates/t1");
    expect(Object.keys(payload).sort()).toEqual(["auto_print_on", "barcode_field", "barcode_types", "body_html", "doc_type", "font_family",
      "is_default", "margin_mm", "name", "page_h_mm", "page_size", "page_w_mm"]);
    expect(payload.body_html).toBe(initial.body_html);
  });

  it("an edit in the HTML view is what gets saved (and new templates are created with POST)", async () => {
    localStorage.setItem("tk_template_view", "html");
    post.mockImplementation(async (p: string) => (p === "/documents/templates" ? { id: "new1" } : { html: "" }));
    render(<TemplateEditorDrawer initial={{ doc_type: "invoice", name: "Invoice" }} onClose={() => {}} />);
    const code = await screen.findByLabelText("HTML source") as HTMLTextAreaElement;
    fireEvent.change(code, { target: { value: "<p>{Client Name} owes {Order Total}</p>" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/documents/templates", expect.objectContaining({ name: "Invoice", body_html: "<p>{Client Name} owes {Order Total}</p>" })));
  });

  it("puts the main action first, on the left of the footer", async () => {
    localStorage.setItem("tk_template_view", "html");
    render(<TemplateEditorDrawer initial={tpl("Bin label", { doc_type: "label" })} onClose={() => {}} />);
    const save = screen.getByRole("button", { name: "Save" });
    const footer = save.parentElement!;
    expect(footer.firstElementChild).toBe(save);
  });
});
