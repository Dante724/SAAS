// The work order's Print menu: one-click default, status-fitting documents, invoices, sticker.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
const printed: string[] = [];
vi.mock("../../lib/safeHtml", () => ({ printHtmlInFrame: (h: string) => printed.push(h), escapeHtml: (s: string) => s }));

import PrintMenu, { pickDefault, STICKER } from "./PrintMenu";

const est = { id: "t-est", name: "Estimate", doc_type: "estimate", print_statuses: ["diagnosed"] };
const wo = { id: "t-wo", name: "Work order", doc_type: "work_order", is_default: true, print_statuses: [] };
const rcpt = { id: "t-rc", name: "Pickup receipt", doc_type: "receipt", print_statuses: ["ready"] };
const inv = { id: "t-inv", name: "Invoice A4", doc_type: "invoice", print_statuses: [] };
const tag = { id: "t-tag", name: "Price tag", doc_type: "label", print_statuses: [] };

let urls: string[] = [];
let invoices: any[] = [];
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  urls = []; printed.length = 0; invoices = [{ id: "i1", invoice_number: "INV-7", status: "issued" }];
  try { localStorage.clear(); } catch { /* noop */ }
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    if (url.includes("/documents/templates?order_id=")) return reply({ items: [est, wo] });   // server filtered by status
    if (url.includes("/documents/templates")) return reply({ items: [est, wo, rcpt, inv, tag] });
    if (url.includes("/cash/orders/")) return reply({ invoices, payments: [] });
    if (url.includes("/documents/render")) return reply({ html: `<p>${url}</p>`, name: "x" });
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("pickDefault", () => {
  it("prefers last used, then a template made for this status, then the default work order, then the sticker", () => {
    expect(pickDefault([est, wo], "t-rc", [est, wo, rcpt])).toBe("t-rc");
    expect(pickDefault([est, wo], null, [est, wo])).toBe("t-est");
    expect(pickDefault([wo], null, [wo])).toBe("t-wo");
    expect(pickDefault([], null, [])).toBe(STICKER);
    expect(pickDefault([wo], "gone", [wo])).toBe("t-wo");            // a deleted template is forgotten
  });
});

describe("PrintMenu", () => {
  const open = async () => {
    render(<PrintMenu orderId="o1" status="diagnosed" onSticker={() => { printed.push("sticker"); }} onError={() => {}} />);
    await screen.findByRole("button", { name: /Print Estimate/ });
    await userEvent.click(screen.getByRole("button", { name: "Choose what to print" }));
  };

  it("lists the sticker and only documents that fit the status, never labels; Show all reveals the rest", async () => {
    await open();
    expect(screen.getByText("Bench sticker")).toBeTruthy();
    expect(screen.getByText("Estimate")).toBeTruthy();
    expect(screen.queryByText("Pickup receipt")).toBeNull();
    expect(screen.queryByText("Price tag")).toBeNull();
    await userEvent.click(screen.getByText("Show all documents"));
    expect(screen.getByText("Pickup receipt")).toBeTruthy();
    expect(screen.queryByText("Price tag")).toBeNull();
  });

  it("prints a document rendered by the server and remembers it as the one-click default", async () => {
    await open();
    await userEvent.click(screen.getByText("Work order"));
    await waitFor(() => expect(printed.length).toBe(1));
    expect(urls.some((u) => u.includes("/documents/render/o1/t-wo"))).toBe(true);
    expect(screen.getByRole("button", { name: /Print Work order/ })).toBeTruthy();
  });

  it("prints invoice templates against the job's latest invoice", async () => {
    await open();
    expect(screen.getByText("Invoice INV-7")).toBeTruthy();
    await userEvent.click(screen.getByText("Invoice A4"));
    await waitFor(() => expect(printed.length).toBe(1));
    expect(urls.some((u) => u.includes("/documents/render-invoice/i1/t-inv"))).toBe(true);
  });

  it("offers no invoice rows when the job has no invoice", async () => {
    invoices = [{ id: "i0", status: "cancelled" }];
    await open();
    expect(screen.queryByText("Invoice A4")).toBeNull();
  });

  it("prints the bench sticker from the menu", async () => {
    await open();
    await userEvent.click(screen.getByText("Bench sticker"));
    expect(printed).toEqual(["sticker"]);
  });

  it("never asks for invoices when the person can't view them", async () => {
    render(<PrintMenu orderId="o1" status="diagnosed" canInvoices={false} onSticker={() => {}} onError={() => {}} />);
    await screen.findByRole("button", { name: /Print Estimate/ });
    await userEvent.click(screen.getByRole("button", { name: "Choose what to print" }));
    expect(screen.queryByText("Invoice A4")).toBeNull();
    expect(urls.some((u) => u.includes("/cash/orders/"))).toBe(false);
  });

  it("prints once on a double click", async () => {
    let release: () => void = () => {};
    const onSticker = vi.fn(() => new Promise<void>((r) => { release = r; }));
    render(<PrintMenu orderId="o2" status="new" onSticker={onSticker} onError={() => {}} />);
    const main = await screen.findByRole("button", { name: /Print / });
    await waitFor(() => expect(urls.some((u) => u.includes("templates?order_id=o2"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Choose what to print" }));
    await userEvent.click(screen.getByText("Bench sticker"));
    await userEvent.click(main);
    expect(onSticker).toHaveBeenCalledTimes(1);
    release();
  });

  it("closes on Escape", async () => {
    await open();
    expect(screen.getByText("Bench sticker")).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText("Bench sticker")).toBeNull());
  });
});
