// Tekly admin app: signs in through 2-step, never stores a token, and shows only the
// actions and fields the person's role allows.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminApp from "./AdminApp";
import ShopPanel from "./ShopPanel";
import type { Me } from "./api";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body }) as Response;
let calls: { url: string; init?: RequestInit }[] = [];

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => { calls = []; });

describe("sign-in", () => {
  it("goes password → code, sends the CSRF header, and never touches localStorage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    let signedIn = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith("/me")) return signedIn ? reply({ id: "a", name: "Ops", email: "o@t.dev", role: { key: "owner", name: "Owner" }, permissions: {}, field_levels: {}, limits: {} }) : reply({ detail: "no" }, 401);
      if (url.endsWith("/login")) return reply({ next: "mfa", ticket: "T" });
      if (url.endsWith("/login/mfa")) { signedIn = true; return reply({ access_token: "secret-token" }); }
      return reply({ segments: { paid: 0, new: 0, trial: 0, suspended: 0 }, my_book: 0, items: [] });
    }));
    render(<AdminApp />);
    await userEvent.type(await screen.findByLabelText("Email"), "o@t.dev");
    await userEvent.type(screen.getByLabelText("Password"), "a-long-password");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.type(await screen.findByLabelText("Code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("Tekly admin");
    await waitFor(() => expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy());
    expect(calls.every((c) => (c.init?.headers as Record<string, string>)["X-Tekly-Admin"] === "1")).toBe(true);
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});

const shop = {
  id: "s1", account_id: "TK-7K4M-92Q", name: "FixIt", plan: "shop", sub_status: "active", segment: "paid",
  sub_start: null, sub_end: null, country: "IN", employees: 3, assigned_admin_id: null, assigned_to: null,
  last_contacted_at: null, notes: [], history: [],
};
function me(perms: string[]): Me {
  return { id: "a", name: "Sam", email: "s@t.dev", role: { key: "sales_am", name: "Sales" },
    permissions: Object.fromEntries(perms.map((p) => [p, true])), field_levels: {}, limits: { max_discount_pct: 15 } };
}

describe("shop panel", () => {
  it("shows only the actions the role allows, and no fields the server left out", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(shop)));
    render(<ShopPanel id="s1" me={me(["give_discount", "add_notes"])} onClose={() => {}} />);
    await screen.findByText("FixIt");
    expect(screen.getByRole("button", { name: "Discount" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Suspend" })).toBeNull();
    expect(screen.queryByText("Owner")).toBeNull();          // owner contact not sent → not shown
    expect(screen.queryByText(/^Discount$/, { selector: "div" })).toBeNull();
    expect(screen.getByText("TK-7K4M-92Q")).toBeTruthy();
  });

  it("tells the person when a discount went to the owner for approval", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      init?.method === "POST" ? reply({ status: "requested", why: "Above your discount limit of 15%" }) : reply(shop)));
    render(<ShopPanel id="s1" me={me(["give_discount"])} onClose={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Discount" }));
    await userEvent.type(screen.getByLabelText("Percent off"), "30");
    await userEvent.type(screen.getByLabelText("Reason"), "Retention");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Sent to the owner for approval/);
  });
});

describe("purge section", () => {
  const owner = (): Me => ({ ...me([]), role: { key: "owner", name: "Owner" }, permissions: { purge_shops: true } });
  const gone = { ...shop, offboarded_at: "2026-09-01T10:00:00",
    purge: { eligible: true, why_not: null, after_days: 30, eligible_from: "2026-10-01T10:00:00", purged_at: null } };

  it("is not shown to a role that can't purge", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(shop)));
    render(<ShopPanel id="s1" me={me(["give_discount"])} onClose={() => {}} />);
    await screen.findByText("FixIt");
    expect(screen.queryByText(/Delete this shop's data/)).toBeNull();
  });

  it("explains why a shop that isn't ready can't be purged, with no button", async () => {
    const early = { ...gone, purge: { ...gone.purge, eligible: false, why_not: "A shop can be purged 30 days after it was offboarded." } };
    vi.stubGlobal("fetch", vi.fn(async () => reply(early)));
    render(<ShopPanel id="s1" me={owner()} onClose={() => {}} />);
    await screen.findByText(/can be purged 30 days after/);
    expect(screen.queryByRole("button", { name: "Check what would be deleted" })).toBeNull();
  });

  it("shows the dry run, and deletes only once the exact name is typed", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith("/purge") && init?.method === "POST") return reply({ status: "purged", total: 12, media_files: 1, counts: {} });
      if (url.endsWith("/purge")) return reply({ eligible: true, why_not: null, after_days: 30, offboarded_at: gone.offboarded_at,
        eligible_from: null, purged_at: null, confirm_name: "FixIt", counts: { orders: 3, clients: 9 }, total: 12, media_files: 1 });
      return reply(gone);
    }));
    render(<ShopPanel id="s1" me={owner()} onClose={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Check what would be deleted" }));
    await screen.findByText("12 records · 1 file");
    expect(screen.getByText("clients")).toBeTruthy();
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);    // the dry run changes nothing
    const del = screen.getByRole("button", { name: "Delete for good" }) as HTMLButtonElement;
    await userEvent.type(screen.getByLabelText("Reason"), "Retention passed");
    await userEvent.type(screen.getByLabelText(/Type the shop's name/), "fixit");
    expect(del.disabled).toBe(true);
    await userEvent.clear(screen.getByLabelText(/Type the shop's name/));
    await userEvent.type(screen.getByLabelText(/Type the shop's name/), "FixIt");
    expect(del.disabled).toBe(false);
    await userEvent.click(del);
    await screen.findByText("Deleted 12 records and 1 file.");
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect(JSON.parse(String(post.init!.body))).toEqual({ confirm_name: "FixIt", reason: "Retention passed" });
  });
});
