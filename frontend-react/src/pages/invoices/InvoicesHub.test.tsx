// Money-safety: a double-click on "Issue e-invoice" must post exactly once.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/BulkPrintDialog", () => ({ default: () => null }));

import InvoicesHub from "./InvoicesHub";

let posts: string[] = [];
let releasePost: () => void = () => {};
const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  posts = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") { posts.push(url); await new Promise<void>((r) => { releasePost = r; }); return reply({}); }
    if (url.startsWith("/api/regions/pack")) return reply({ einvoice: { standard: "peppol" } });
    if (url.startsWith("/api/finance/invoices")) return reply({ items: [{ id: "i1", invoice_number: "INV-1", status: "issued", total: 10, einvoice_status: "none" }] });
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("InvoicesHub e-invoice", () => {
  it("double-clicking Issue e-invoice posts once and disables the button", async () => {
    const u = userEvent.setup();
    render(<InvoicesHub />);
    await u.dblClick(await screen.findByRole("button", { name: "Issue e-invoice" }));
    expect(posts).toEqual(["/api/finance/invoices/i1/einvoice"]);
    expect((screen.getByRole("button", { name: "Issuing…" }) as HTMLButtonElement).disabled).toBe(true);
    releasePost();
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: "Issuing…" })).toBeNull());
    expect(posts).toHaveLength(1);
  });
});
