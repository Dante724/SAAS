import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within, act } from "@testing-library/react";
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
const confirm = vi.fn(async () => true);
vi.mock("../../lib/dialog", () => ({ confirmDialog: (...a: any[]) => (confirm as any)(...a) }));

import TemplateEditorDrawer, { pickView, AUTOSAVE_MS } from "./TemplateEditorDrawer";

const GROUPS = [{ group: "Client", items: ["{Client Name}", "{Client Phone}"] }];
const tpl = (name: string, extra: Record<string, unknown> = {}) => ({
  id: "t1", name, doc_type: "invoice", body_html: SEEDED_TEMPLATES[name], page_size: "a4", font_family: "Inter", is_default: false,
  auto_print_on: "", page_w_mm: null, page_h_mm: null, margin_mm: null, barcode_field: null, barcode_types: {}, ...extra,
});
// Vendor names and badges that must never reach the screen.
const VENDOR = /powered by|tiptap|grapes|tinymce|prosemirror|made with|\bgjs\b/i;
const PREF = "tk_template_view:me";
const preview = (body: string) => {
  // The server fills fields: here {Client Name} → Neha Gupta, everything else left as is.
  return { html: body.replace(/\{Client Name\}/g, "Neha Gupta"), sample: "WO-1001" };
};

beforeEach(() => {
  get.mockReset(); post.mockReset(); put.mockReset(); confirm.mockClear();
  get.mockImplementation(async (p: string) => (p === "/documents/templates" ? { items: [], variable_groups: GROUPS } : { items: [] }));
  post.mockImplementation(async (p: string, b: any) => (p === "/documents/preview" ? preview(b.body_html) : p === "/documents/ai-generate" ? { body_html: "<p>{Client Name}</p>" } : { id: "new1" }));
  put.mockResolvedValue({});
  localStorage.clear();
});

async function openCode() {
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Edit code" }));
  return await screen.findByLabelText("HTML source") as HTMLTextAreaElement;
}

describe("pickView", () => {
  const ok = { design: null, text: null, html: null };
  it("opens the last visual view used if it can hold the template, else Design, Text, code", () => {
    expect(pickView(ok, "text")).toBe("text");
    expect(pickView(ok, "html")).toBe("design");
    expect(pickView(ok, null)).toBe("design");
    expect(pickView({ ...ok, design: "no" }, "design")).toBe("text");
    expect(pickView({ design: "no", text: "no", html: null }, null)).toBe("html");
  });
});

describe("TemplateEditorDrawer", () => {
  it("greys out Design and Text with a reason, and offers a safe fix it explains first", async () => {
    render(<TemplateEditorDrawer initial={tpl("Sales receipt", { doc_type: "receipt" })} onClose={() => {}} />);
    const reason = await screen.findByTestId("reason-design", {}, { timeout: 5000 });
    expect(screen.getByRole("button", { name: /Design/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Text/ })).toBeDisabled();
    expect(reason.textContent).toMatch(/\{#if Total Discount\} between table rows/);
    // falls back to the code, showing the template exactly as stored
    expect((screen.getByLabelText("HTML source") as HTMLTextAreaElement).value).toBe(SEEDED_TEMPLATES["Sales receipt"]);
    fireEvent.click(within(reason).getByRole("button", { name: /Make this editable visually/ }));
    const dlg = screen.getByRole("dialog", { name: "Make this editable visually" });
    expect(dlg.textContent).toMatch(/prints the same/i);
    expect(dlg.textContent).toContain('data-tk-show="{#if Total Discount}yes{/if}"');
    expect(put).not.toHaveBeenCalled();
    fireEvent.click(within(dlg).getByRole("button", { name: "Make editable" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Design/ })).toBeEnabled());
  });

  it("shows the shop's real data in Text, field names with Show fields, and saves the fields either way", async () => {
    localStorage.setItem(PREF, "text");
    const { container } = render(<TemplateEditorDrawer initial={tpl("Credit note", { doc_type: "credit_note" })} onClose={() => {}} />);
    await waitFor(() => expect(container.querySelector(".tk-text-page")).toBeTruthy(), { timeout: 5000 });
    await waitFor(() => expect(container.querySelector(".tk-text-page")!.textContent).toContain("Neha Gupta"));
    expect(container.querySelector(".tk-text-page .tok-sample")).toBeTruthy();         // no data: a clearly fake sample
    expect(container.textContent || "").not.toMatch(VENDOR);
    fireEvent.click(screen.getByRole("switch", { name: "Show fields" }));
    await waitFor(() => expect(Array.from(container.querySelectorAll(".tk-text-page .tok-pill")).map((e) => e.textContent)).toContain("Client Name"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls[0][1].body_html).toBe(SEEDED_TEMPLATES["Credit note"]);
  });

  it("opens in Design with Tekly's own blocks, one fields list and a simple style panel", async () => {
    const { container } = render(<TemplateEditorDrawer initial={tpl("Estimate", { doc_type: "estimate" })} onClose={() => {}} />);
    const panel = within(await screen.findByRole("complementary", { name: "Blocks and fields" }, { timeout: 5000 }));
    for (const b of ["Text", "Heading", "Image", "Shop logo", "Divider", "Two columns", "Items table", "Totals", "Signature line"]) {
      expect(panel.getByRole("button", { name: new RegExp(`^${b}$`) })).toBeInTheDocument();
    }
    expect(panel.getByRole("button", { name: "Client Name" })).toBeInTheDocument();     // first group open
    expect(screen.queryByRole("button", { name: /Variables/ })).toBeNull();            // no second list
    expect(screen.queryByRole("button", { name: /HTML/ })).toBeNull();                 // code is in More
    expect(container.textContent || "").not.toMatch(VENDOR);
    expect(container.querySelector(".gjs-pn-panels, .gjs-logo")).toBeNull();
  });

  it("saves an untouched template with the same payload as before, body byte for byte", async () => {
    localStorage.setItem(PREF, "text");
    const initial = tpl("Estimate", { doc_type: "estimate" });
    const { container } = render(<TemplateEditorDrawer initial={initial} onClose={() => {}} />);
    await waitFor(() => expect(container.querySelector(".tk-text-page")).toBeTruthy(), { timeout: 5000 });
    expect(screen.getByTestId("save-status").textContent).toMatch(/All changes saved/);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    const [path, payload] = put.mock.calls[0];
    expect(path).toBe("/documents/templates/t1");
    expect(Object.keys(payload).sort()).toEqual(["auto_print_on", "barcode_field", "barcode_types", "body_html", "doc_type", "font_family",
      "is_default", "margin_mm", "name", "page_h_mm", "page_size", "page_w_mm"]);
    expect(payload.body_html).toBe(initial.body_html);
  });

  it("autosaves once, about two seconds after the last change, and Ctrl/Cmd+S saves at once", async () => {
    render(<TemplateEditorDrawer initial={tpl("Bin label", { doc_type: "label" })} onClose={() => {}} />);
    await screen.findByRole("complementary", { name: "Blocks and fields" }, { timeout: 5000 });
    const code = await openCode();
    fireEvent.change(code, { target: { value: "<p>{Client Name} one</p>" } });
    await act(() => new Promise((r) => setTimeout(r, 600)));
    fireEvent.change(code, { target: { value: "<p>{Client Name} two</p>" } });
    expect(screen.getByTestId("save-status").textContent).toMatch(/Unsaved changes/);
    await act(() => new Promise((r) => setTimeout(r, AUTOSAVE_MS - 600)));
    expect(put).not.toHaveBeenCalled();                                   // not while still typing
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1), { timeout: 3000 });
    expect(put.mock.calls[0][1].body_html).toBe("<p>{Client Name} two</p>");
    await waitFor(() => expect(screen.getByTestId("save-status").textContent).toMatch(/All changes saved/));
    await act(() => new Promise((r) => setTimeout(r, AUTOSAVE_MS + 300)));
    expect(put).toHaveBeenCalledTimes(1);                                 // nothing changed: no extra saves

    fireEvent.change(code, { target: { value: "<p>{Client Name} three</p>" } });
    fireEvent.keyDown(window, { key: "s", metaKey: true });
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls[1][1].body_html).toBe("<p>{Client Name} three</p>");
  }, 15000);

  it("warns before closing with unsaved changes", async () => {
    const onClose = vi.fn();
    render(<TemplateEditorDrawer initial={tpl("Bin label", { doc_type: "label" })} onClose={onClose} />);
    await screen.findByRole("complementary", { name: "Blocks and fields" }, { timeout: 5000 });
    const code = await openCode();
    fireEvent.change(code, { target: { value: "<p>changed</p>" } });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    const dlg = await screen.findByRole("dialog", { name: "Unsaved changes" });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(within(dlg).getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByRole("dialog", { name: "Unsaved changes" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Unsaved changes" })).getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(put.mock.calls.at(-1)![1].body_html).toBe("<p>changed</p>");
  });

  it("Undo all changes puts back the version that was opened", async () => {
    render(<TemplateEditorDrawer initial={tpl("Bin label", { doc_type: "label" })} onClose={() => {}} />);
    await screen.findByRole("complementary", { name: "Blocks and fields" }, { timeout: 5000 });
    const code = await openCode();
    fireEvent.change(code, { target: { value: "<p>oops</p>" } });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Undo all changes" }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    await screen.findByRole("complementary", { name: "Blocks and fields" }, { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls.at(-1)![1].body_html).toBe(SEEDED_TEMPLATES["Bin label"]);
  });

  it("clicking a field puts it in at the cursor (code view)", async () => {
    render(<TemplateEditorDrawer initial={tpl("Bin label", { doc_type: "label" })} onClose={() => {}} />);
    await screen.findByRole("complementary", { name: "Blocks and fields" }, { timeout: 5000 });
    const code = await openCode();
    fireEvent.change(code, { target: { value: "<p>Hi </p>" } });
    code.setSelectionRange(6, 6);
    fireEvent.click(within(screen.getByRole("complementary", { name: "Fields list" })).getByRole("button", { name: "Client Name" }));
    expect(code.value).toBe("<p>Hi {Client Name}</p>");
  });

  it("New shows the layout gallery, and picking one creates the template through the existing API", async () => {
    const onlyGet = get.getMockImplementation()!;
    get.mockImplementation(async (p: string) => (p === "/documents/templates"
      ? { items: [{ id: "s1", name: "Price tag", doc_type: "label", page_size: "label", page_w_mm: 40, page_h_mm: 30, body_html: SEEDED_TEMPLATES["Price tag"] }], variable_groups: GROUPS }
      : onlyGet(p)));
    render(<TemplateEditorDrawer initial={null} onClose={() => {}} />);
    const gallery = await screen.findByRole("dialog", { name: "Start from a layout" });
    const invoice = await within(gallery).findByRole("button", { name: /Invoice/ });
    expect(within(gallery).getByRole("button", { name: /Price label/ })).toBeInTheDocument();
    fireEvent.click(invoice);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/documents/templates", expect.objectContaining({ name: "Invoice", doc_type: "invoice", page_size: "a4", body_html: "<p>{Client Name}</p>" })));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Start from a layout" })).toBeNull());
    expect((screen.getByLabelText("Template name") as HTMLInputElement).value).toBe("Invoice");
  });

  it("has a visible paper choice and labelled top-bar buttons; the main action is first in the footer", async () => {
    render(<TemplateEditorDrawer initial={tpl("Bin label", { doc_type: "label" })} onClose={() => {}} />);
    const paper = screen.getByLabelText("Paper size") as HTMLSelectElement;
    expect(Array.from(paper.options).map((o) => o.textContent)).toEqual(["A4", "Letter", "80 mm receipt"]);
    for (const n of ["Close", "New", "More", "Page & print"]) expect(screen.getByRole("button", { name: new RegExp(n) })).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save.parentElement!.firstElementChild).toBe(save);
  });
});
