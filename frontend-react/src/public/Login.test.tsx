import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Login from "./Login";

const CODE = "123456";
let sent: FormData[] = [];

function reply(status: number, body: unknown, headers: Record<string, string> = {}) {
  return { ok: status < 400, status, headers: new Headers(headers), json: async () => body } as Response;
}

beforeEach(() => {
  sent = [];
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, replace: vi.fn() } });
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    const fd = init!.body as FormData;
    sent.push(fd);
    const code = fd.get("client_secret");
    if (code === CODE) return reply(200, { access_token: "tok-1" });
    return reply(401, { detail: "MFA code required or invalid" }, { "X-MFA-Required": "1" });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("Login", () => {
  it("asks for the 2-step code after the password, then signs in with it", async () => {
    const u = userEvent.setup();
    render(<Login />);
    await u.type(screen.getByLabelText("Email"), "owner@shop.com");
    await u.click(screen.getByRole("button", { name: "Continue" }));
    await u.type(await screen.findByLabelText("Password"), "pw-12345678");
    await u.click(screen.getByRole("button", { name: "Sign in" }));

    // Second step: a code box, no error shown for simply needing the code.
    const box = await screen.findByLabelText("Code");
    expect(screen.getByText("2-step verification")).toBeTruthy();
    expect(screen.queryByText(/didn't work/)).toBeNull();
    expect(sent[0].get("client_secret")).toBeNull();

    // A wrong code keeps the box and says so plainly.
    await u.type(box, "000000");
    await u.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/That code didn't work/)).toBeTruthy();

    await u.type(screen.getByLabelText("Code"), "123 456");
    await u.click(screen.getByRole("button", { name: "Verify" }));
    expect(sent).toHaveLength(3);
    expect(sent[2].get("username")).toBe("owner@shop.com");
    expect(sent[2].get("password")).toBe("pw-12345678");
    expect(sent[2].get("client_secret")).toBe(CODE);
    await vi.waitFor(() => expect(localStorage.getItem("ros_tok")).toBe("tok-1"));
    expect(window.location.replace).toHaveBeenCalledWith("/");
  });

  it("can go back to the email step", async () => {
    const u = userEvent.setup();
    render(<Login />);
    await u.type(screen.getByLabelText("Email"), "owner@shop.com");
    await u.click(screen.getByRole("button", { name: "Continue" }));
    await u.type(await screen.findByLabelText("Password"), "pw-12345678");
    await u.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByLabelText("Code");
    await u.click(screen.getByRole("button", { name: "Use a different account" }));
    expect(screen.getByLabelText("Email")).toHaveProperty("value", "owner@shop.com");
    expect(screen.queryByLabelText("Code")).toBeNull();
    await u.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByLabelText("Password")).toHaveProperty("value", "");
  });

  it("asks for a real email before the password step", async () => {
    const u = userEvent.setup();
    render(<Login />);
    await u.type(screen.getByLabelText("Email"), "owner");
    await u.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert").textContent).toMatch(/email/);
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(sent).toHaveLength(0);
  });
});
