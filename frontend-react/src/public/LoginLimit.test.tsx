import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Login from "./Login";

// At the shop's device limit: the person picks one of their devices to sign out, and signing in
// finishes without typing the password again.

function reply(status: number, body: unknown, headers: Record<string, string> = {}) {
  return { ok: status < 400, status, headers: new Headers(headers), json: async () => body } as Response;
}

const LIMIT = {
  message: "You're signed in on 2 devices", limit: 2, limit_token: "lt-1",
  sessions: [
    { id: "s1", device: "Chrome on Mac", signed_in_at: null, last_active_at: new Date().toISOString() },
    { id: "s2", device: "Safari on iPhone", signed_in_at: null, last_active_at: null },
  ],
};
let calls: { url: string; init?: RequestInit }[] = [];
let store: Map<string, string>;

beforeEach(() => {
  calls = [];
  store = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, replace: vi.fn() } });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/api/auth/token") return reply(409, { detail: LIMIT }, { "X-Session-Limit": "1" });
    if (url === "/api/auth/session-limit/continue") return reply(200, { access_token: "tok-new" });
    return reply(404, {});
  }));
});
afterEach(() => vi.unstubAllGlobals());

async function toLimitStep() {
  const u = userEvent.setup();
  render(<Login />);
  await u.type(screen.getByLabelText("Email"), "owner@shop.com");
  await u.click(screen.getByRole("button", { name: "Continue" }));
  await u.type(await screen.findByLabelText("Password"), "pw-12345678");
  await u.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByText("You're signed in on too many devices");
  return u;
}

describe("Login at the device limit", () => {
  it("lists the devices and asks for one to be signed out", async () => {
    const u = await toLimitStep();
    expect(screen.getByText(/allows 2 devices per person/)).toBeTruthy();
    expect(screen.getByText("Chrome on Mac")).toBeTruthy();
    expect(screen.getByText("Active now")).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Sign out of it and continue" }));
    expect(await screen.findByText("Choose a device to sign out")).toBeTruthy();
    expect(calls.some((c) => c.url === "/api/auth/session-limit/continue")).toBe(false);
  });

  it("signs the chosen device out and finishes signing in", async () => {
    const u = await toLimitStep();
    await u.click(screen.getByRole("radio", { name: /Safari on iPhone/ }));
    await u.click(screen.getByRole("button", { name: "Sign out of it and continue" }));
    await vi.waitFor(() => expect(store.get("ros_tok")).toBe("tok-new"));
    const sent = calls.find((c) => c.url === "/api/auth/session-limit/continue")!;
    expect(JSON.parse(String(sent.init!.body))).toEqual({ limit_token: "lt-1", session_id: "s2" });
    expect(window.location.replace).toHaveBeenCalledWith("/");
  });

  it("can cancel back to the email step", async () => {
    const u = await toLimitStep();
    await u.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByLabelText("Email")).toBeTruthy();
  });
});
