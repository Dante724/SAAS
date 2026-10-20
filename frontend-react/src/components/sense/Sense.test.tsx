// Tekly Sense: suggestions explain themselves and the owner's switches save.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobSuggestions, MorningBriefCard, SenseSettingsSection, WatchdogPanel, loadSense } from "./Sense";
import { MeProvider } from "../../context/Me";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const SETTINGS = { enabled: true, features: { job_suggestions: true, deferred: true, ready_time_why: true, margin_check: true,
  morning_brief: true, added_together: true, service_due: true, watchdog: true },
  brief: { recipients: [], hour: 7, email: false, push: false },
  watchdog: { discount_pct: 20, cash_tolerance: 10 },
  learning: { closed_jobs: 12, needed: 20, ready: false } };

let calls: { url: string; method: string; body?: any }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url.includes("/sense/settings") && method === "PUT") return reply({ ...SETTINGS, features: { ...SETTINGS.features, ...(body.features || {}) }, brief: { ...SETTINGS.brief, ...(body.brief || {}) } });
    if (url.includes("/auth/me")) return reply({ id: "o1", name: "Owner", is_shop_owner: true, permissions: {} });
    if (url.includes("/sense/watchdog/review")) return reply({ ok: true });
    if (url.includes("/sense/watchdog")) return reply({ enabled: true, window_days: 30, scorecard: [], flags: [
      { key: "big_discounts:e2:2026-10-18", kind: "big_discounts", employee_name: "Ana", title: "Unusual number of big discounts — Ana",
        rule: "Discounts of 20% or more in the last 30 days", count: 5, others_median: 0.5, amount: 300,
        evidence: [{ date: "2026-10-18", ref: "WO-1", what: "Screen", pct: 30, amount: 60 }] }] });
    if (url.includes("/staff/employees")) return reply([{ id: "e2", name: "Ana", active: true }]);
    if (url.includes("/sense/brief")) return reply({ enabled: true, yesterday: { jobs_closed: 3, paid: 410 }, today: { due: 2 },
      attention: [{ key: "stuck", title: "1 job in the same status for 5+ days", count: 1, to: "/orders" }] });
    if (url.includes("/sense/settings")) return reply(SETTINGS);
    if (url.includes("/sense/draft")) return reply({ enabled: true, learning: false, items: [
      { description: "Screen replacement", kind: "service", unit_price: 129, why: { used: 12, similar: 12 } }] });
    return reply({});
  }));
  loadSense(true);                      // fresh settings per test: no state carried between tests
});
afterEach(() => vi.unstubAllGlobals());

describe("Tekly Sense", () => {
  it("suggests a line with its usual price, explains why, and adds it on tap", async () => {
    const u = userEvent.setup();
    const onAdd = vi.fn();
    render(<JobSuggestions complaint="cracked screen" onAdd={onAdd} />);
    const chip = await screen.findByRole("button", { name: /^\+ Screen replacement/ }, { timeout: 2000 });
    await u.click(screen.getByRole("button", { name: "Why?" }));
    expect(screen.getByRole("note").textContent).toContain("Used on 12 of your last 12 similar jobs");
    await u.click(chip);
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ description: "Screen replacement", price: 129 }));
  });

  it("shows how far the shop is from suggestions and saves a switch straight away", async () => {
    const u = userEvent.setup();
    render(<SenseSettingsSection />);
    expect(await screen.findByText("12 of 20 closed jobs")).toBeTruthy();
    await u.click(screen.getByRole("switch", { name: "Margin check" }));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({ features: { margin_check: false } });
    expect(screen.getByRole("switch", { name: "Margin check" }).getAttribute("aria-checked")).toBe("false");
  });

  it("lets the owner choose email and who else gets the brief", async () => {
    const u = userEvent.setup();
    render(<SenseSettingsSection />);
    await u.click(await screen.findByRole("checkbox", { name: "Email" }));
    expect(calls.filter((c) => c.method === "PUT").pop()?.body).toEqual({ brief: { email: true } });
    await u.click(await screen.findByRole("checkbox", { name: "Ana" }));
    expect(calls.filter((c) => c.method === "PUT").pop()?.body).toEqual({ brief: { recipients: ["e2"] } });
  });

  it("shows the brief on the Today page and opens what needs a look", async () => {
    const u = userEvent.setup();
    const onOpen = vi.fn();
    render(<MorningBriefCard onOpen={onOpen} />);
    expect(await screen.findByText("3 jobs closed")).toBeTruthy();
    expect(screen.getByText("2 jobs due")).toBeTruthy();
    await u.click(screen.getByRole("button", { name: /same status/ }));
    expect(onOpen).toHaveBeenCalledWith("/orders");
  });

  it("shows the owner an unusual pattern with its evidence, and hides it once reviewed", async () => {
    const u = userEvent.setup();
    render(<MeProvider><WatchdogPanel /></MeProvider>);
    expect(await screen.findByText("Unusual number of big discounts — Ana")).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Why?" }));
    expect(screen.getByText(/others usually 0.5/)).toBeTruthy();
    expect(screen.getByText(/WO-1/)).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Mark reviewed" }));
    expect(calls.find((c) => c.url.includes("/watchdog/review"))?.body).toEqual({ key: "big_discounts:e2:2026-10-18" });
    expect(await screen.findByText("Nothing unusual.")).toBeTruthy();
  });
});
