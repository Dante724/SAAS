// Tekly Sense: suggestions explain themselves and the owner's switches save.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobSuggestions, SenseSettingsSection, loadSense } from "./Sense";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const SETTINGS = { enabled: true, features: { job_suggestions: true, deferred: true, ready_time_why: true, margin_check: true },
  learning: { closed_jobs: 12, needed: 20, ready: false } };

let calls: { url: string; method: string; body?: any }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url.includes("/sense/settings") && method === "PUT") return reply({ ...SETTINGS, features: { ...SETTINGS.features, ...(body.features || {}) } });
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
});
