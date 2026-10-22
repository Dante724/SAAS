// Money-safety on the Payroll tab: "Mark paid" asks first, can't double-post, and
// payroll errors are shown instead of swallowed.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/DateField", () => ({ default: () => <input aria-label="date" /> }));

import FinanceHub from "./FinanceHub";

let posts: string[] = [];
let bodies: unknown[] = [];
let failPost = false;
let runStatus = "draft";
const reply = (status: number, body: unknown) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => (typeof body === "string" ? body : JSON.stringify(body)) }) as Response;

beforeEach(() => {
  posts = []; bodies = []; failPost = false; runStatus = "draft";
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") { posts.push(url); bodies.push(JSON.parse(String(init.body || "{}"))); return failPost ? reply(500, "boom") : reply(200, {}); }
    if (url.startsWith("/api/payroll/runs")) return reply(200, { items: [{ id: "r1", period_start: "2026-10-01", period_end: "2026-10-31", status: runStatus }] });
    if (url.startsWith("/api/cash/summary")) return reply(200, { accounts: [{ id: "a1", name: "Main till" }] });
    return reply(200, { items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const renderPayroll = () => render(<MemoryRouter><FinanceHub initialTab="payroll" /></MemoryRouter>);

describe("FinanceHub payroll", () => {
  it("Mark paid asks for confirmation; Cancel posts nothing", async () => {
    const u = userEvent.setup();
    renderPayroll();
    await u.click(await screen.findByRole("button", { name: "Mark paid" }));
    await u.click(await screen.findByRole("button", { name: "Cancel" }));
    await new Promise((r) => setTimeout(r, 0));
    expect(posts).toHaveLength(0);
  });

  it("Mark paid posts once after confirming, even on a double-click", async () => {
    const u = userEvent.setup();
    renderPayroll();
    await u.dblClick(await screen.findByRole("button", { name: "Mark paid" }));
    // Only one confirmation opens.
    const dialogs = await screen.findAllByText(/Mark this payroll run as paid/);
    expect(dialogs).toHaveLength(1);
    const confirm = screen.getAllByRole("button", { name: "Mark paid" }).pop()!;
    await u.click(confirm);
    await vi.waitFor(() => expect(posts).toEqual(["/api/payroll/runs/r1/pay"]));
    // the payout is taken out of a real account
    expect(bodies).toEqual([{ account_id: "a1" }]);
  });

  it("Void asks for a reason and posts it", async () => {
    const u = userEvent.setup();
    runStatus = "paid";
    renderPayroll();
    await u.click(await screen.findByRole("button", { name: "Void" }));
    await u.type(await screen.findByRole("textbox"), "Wrong period");
    await u.click(screen.getByRole("button", { name: "Void run" }));
    await vi.waitFor(() => expect(posts).toEqual(["/api/payroll/runs/r1/void"]));
    expect(bodies).toEqual([{ reason: "Wrong period" }]);
    expect(screen.queryByRole("button", { name: "Mark paid" })).toBeNull();
  });

  it("a failed payroll run is shown, not swallowed", async () => {
    const u = userEvent.setup();
    failPost = true;
    renderPayroll();
    await u.click(await screen.findByRole("button", { name: "Run payroll (draft)" }));
    expect(await screen.findByText(/Could not run payroll/)).toBeTruthy();
    expect(posts).toEqual(["/api/payroll/run-all"]);
  });
});
