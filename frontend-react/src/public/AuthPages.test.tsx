import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AcceptInvite, ResetPassword, Signup } from "./AuthPages";

let calls: { url: string; body: any }[] = [];
let replies: Record<string, { status: number; body: any }> = {};

function setUrl(search: string) {
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, search, replace: vi.fn() } });
}

beforeEach(() => {
  calls = []; replies = {};
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) });
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
  setUrl("");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const r = replies[url] ?? { status: 200, body: {} };
    return { ok: r.status < 400, status: r.status, json: async () => r.body } as Response;
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("auth pages", () => {
  it("without a token, reset password asks for the email and sends the link", async () => {
    const u = userEvent.setup();
    render(<ResetPassword />);
    expect(screen.getByText("Reset your password")).toBeTruthy();
    await u.type(screen.getByLabelText("Email"), "owner@shop.com");
    await u.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByText("Check your email")).toBeTruthy();
    expect(calls).toEqual([{ url: "/api/auth/password/forgot", body: { email: "owner@shop.com" } }]);
  });

  it("with a token, the new passwords must match before anything is sent", async () => {
    setUrl("?token=abc");
    const u = userEvent.setup();
    render(<ResetPassword />);
    await u.type(screen.getByLabelText("New password"), "longenough1");
    await u.type(screen.getByLabelText("Confirm password"), "different22");
    await u.click(screen.getByRole("button", { name: "Set new password" }));
    expect(screen.getByRole("alert").textContent).toMatch(/do not match/);
    expect(calls).toHaveLength(0);
  });

  it("sign up asks for the country, which sets currency and timezone", async () => {
    replies["/api/auth/signup-options"] = { status: 200, body: {
      templates: [{ key: "phones", label: "Phones" }], regions: [{ key: "eu", label: "Europe" }], region_defaults: {}, employee_brackets: ["1"],
      countries: [{ code: "US", label: "United States", currency_code: "USD", timezone: "America/New_York" },
                  { code: "GB", label: "United Kingdom", currency_code: "GBP", timezone: "Europe/London" }],
      country_hint: "GB" } };
    replies["/api/auth/register"] = { status: 200, body: { access_token: "tok-uk" } };
    const u = userEvent.setup();
    render(<Signup />);
    const country = await screen.findByLabelText("Country") as HTMLSelectElement;
    await vi.waitFor(() => expect(country.value).toBe("GB"));
    expect((screen.getByLabelText("Currency") as HTMLInputElement).value).toBe("GBP");
    await u.selectOptions(country, "US");
    expect((screen.getByLabelText("Currency") as HTMLInputElement).value).toBe("USD");
    await u.type(screen.getByLabelText("Shop name"), "Fix-It NYC");
    await u.click(screen.getByRole("button", { name: "Continue" }));
    await u.type(await screen.findByLabelText("Your name"), "Sam");
    await u.type(screen.getByLabelText("Email"), "sam@fixit.us");
    await u.type(screen.getByLabelText("Password (8+ characters)"), "pw-12345678");
    await u.click(screen.getByLabelText(/I agree to the/));
    await u.click(screen.getByRole("button", { name: "Create shop" }));
    await vi.waitFor(() => expect(localStorage.getItem("ros_tok")).toBe("tok-uk"));
    const reg = calls.find((c) => c.url === "/api/auth/register")!.body;
    expect(reg).toMatchObject({ country: "US", currency_code: "USD", region: null });
    expect(String(reg.timezone).startsWith("America/")).toBe(true);
  });

  it("a Canadian shop picks its province, which decides GST or HST", async () => {
    replies["/api/auth/signup-options"] = { status: 200, body: {
      templates: [{ key: "phones", label: "Phones" }], regions: [], region_defaults: {}, employee_brackets: ["1"],
      countries: [{ code: "CA", label: "Canada", currency_code: "CAD", timezone: "America/Toronto" },
                  { code: "US", label: "United States", currency_code: "USD", timezone: "America/New_York" }],
      country_hint: "CA",
      provinces: { CA: [{ code: "BC", label: "British Columbia", tax_name: "GST", tax_rate: 5 },
                        { code: "ON", label: "Ontario", tax_name: "HST", tax_rate: 13 }] } } };
    replies["/api/auth/register"] = { status: 200, body: { access_token: "tok-ca" } };
    const u = userEvent.setup();
    render(<Signup />);
    const province = await screen.findByLabelText("Province") as HTMLSelectElement;
    expect(province.value).toBe("");                                   // blank: the server goes by the time zone
    await u.selectOptions(province, "ON");
    expect(screen.getByRole("option", { name: "Ontario · HST 13%" })).toBeTruthy();
    await u.type(screen.getByLabelText("Shop name"), "Respawn");
    await u.click(screen.getByRole("button", { name: "Continue" }));
    await u.type(await screen.findByLabelText("Your name"), "Ash");
    await u.type(screen.getByLabelText("Email"), "ash@respawn.ca");
    await u.type(screen.getByLabelText("Password (8+ characters)"), "pw-12345678");
    await u.click(screen.getByLabelText(/I agree to the/));
    await u.click(screen.getByRole("button", { name: "Create shop" }));
    await vi.waitFor(() => expect(localStorage.getItem("ros_tok")).toBe("tok-ca"));
    expect(calls.find((c) => c.url === "/api/auth/register")!.body).toMatchObject({ country: "CA", province: "ON" });
  });

  it("the province picker is only for Canada", async () => {
    replies["/api/auth/signup-options"] = { status: 200, body: {
      templates: [], regions: [], region_defaults: {}, employee_brackets: ["1"],
      countries: [{ code: "US", label: "United States", currency_code: "USD", timezone: "America/New_York" }],
      country_hint: "US", provinces: { CA: [{ code: "ON", label: "Ontario", tax_name: "HST", tax_rate: 13 }] } } };
    render(<Signup />);
    await screen.findByLabelText("Country");
    expect(screen.queryByLabelText("Province")).toBeNull();
  });

  it("sign up takes the shop first, then the person, and creates both", async () => {
    replies["/api/auth/signup-options"] = { status: 200, body: { templates: [{ key: "tools", label: "Tools Repair" }], regions: [{ key: "in", label: "India" }], region_defaults: { in: { currency_code: "INR" } }, employee_brackets: ["1-5"] } };
    replies["/api/auth/register"] = { status: 200, body: { access_token: "tok-9" } };
    const u = userEvent.setup();
    render(<Signup />);
    await u.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert").textContent).toMatch(/shop's name/);
    await u.type(screen.getByLabelText("Shop name"), "Fix-It Pro");
    await u.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Now, about you")).toBeTruthy();
    await u.type(screen.getByLabelText("Your name"), "Asha");
    await u.type(screen.getByLabelText("Email"), "asha@fixit.in");
    await u.type(screen.getByLabelText("Password (8+ characters)"), "pw-12345678");
    await u.click(screen.getByRole("button", { name: "Create shop" }));
    expect(screen.getByRole("alert").textContent).toMatch(/Terms of service/);   // must tick the box first
    expect(calls.find((c) => c.url === "/api/auth/register")).toBeUndefined();
    await u.click(screen.getByLabelText(/I agree to the/));
    await u.click(screen.getByRole("button", { name: "Create shop" }));
    await vi.waitFor(() => expect(localStorage.getItem("ros_tok")).toBe("tok-9"));
    const reg = calls.find((c) => c.url === "/api/auth/register")!.body;
    expect(reg).toMatchObject({ org_name: "Fix-It Pro", name: "Asha", email: "asha@fixit.in", industry_template: "tools", region: "in", currency_code: "INR", accept_terms: true });
  });

  it("an invalid invite says so plainly", async () => {
    setUrl("?token=nope");
    replies["/api/auth/invite/preview"] = { status: 404, body: { detail: "Invite not found" } };
    render(<AcceptInvite />);
    expect(await screen.findByText("This invite didn't work")).toBeTruthy();
  });
});
