// apih1 + WS-C: API keys card — access picker (read only by default, or only some things),
// expiry, per-key limit, show the key once, rotate.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../context/Me", async (orig) => {
  const real: any = await orig();
  return { ...real, useMe: () => ({ can: () => true }) };
});
vi.mock("../../lib/dialog", () => ({ confirmDialog: async () => true, alertDialog: async () => {} }));
import ApiKeysCard, { accessText, lastUsedText, limitText, scopesFrom } from "./ApiKeysCard";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let keys: any[] = [];
let posts: { url: string; body: any }[] = [];
beforeEach(() => {
  posts = [];
  keys = [{ id: "k1", name: "Zapier", prefix: "rok_ab12cd34", scopes: "read", scope_list: ["read"], status: "active", active: true,
    expires_at: "2027-10-25T00:00:00", last_used_at: null, previous_expires_at: null, creator_inactive: false }];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = init.body ? JSON.parse(String(init.body)) : {};
      posts.push({ url, body });
      return reply({ ...keys[0], key: url.includes("/rotate") ? "rok_new_rotated" : "rok_ab12cd34_secretvalue" }, url.includes("/rotate") ? 200 : 201);
    }
    if (url.includes("/api-keys")) return reply({ items: keys });
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("ApiKeysCard", () => {
  it("creates a read-only key by default with a one-year expiry and shows it once", async () => {
    const u = userEvent.setup();
    render(<ApiKeysCard />);
    expect(await screen.findByText("Zapier")).toBeInTheDocument();
    expect(screen.getByText(/Read only · 120 requests a minute · Never used · Expires/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /Create key/ }));
    const dlg = await screen.findByRole("dialog", { name: "Create API key" });
    expect(within(dlg).getByLabelText(/Read only/)).toBeChecked();
    expect(within(dlg).getByLabelText("Expires after")).toHaveValue("1 year");
    await u.type(within(dlg).getByLabelText("Name"), "Website");
    await u.click(within(dlg).getByRole("button", { name: "Create key" }));
    const shown = await screen.findByRole("dialog", { name: "Your new API key" });
    expect(within(shown).getByTestId("fresh-key")).toHaveTextContent("rok_ab12cd34_secretvalue");
    expect(posts[0].body).toEqual({ name: "Website", scopes: ["read"], expires_in_days: 365 });
    await u.click(within(shown).getByRole("button", { name: "I've saved it" }));
    expect(screen.queryByTestId("fresh-key")).toBeNull();
  });

  it("sends write, cost prices and a never-expiring choice", async () => {
    const u = userEvent.setup();
    render(<ApiKeysCard />);
    await screen.findByText("Zapier");
    await u.click(screen.getByRole("button", { name: /Create key/ }));
    const dlg = await screen.findByRole("dialog", { name: "Create API key" });
    await u.type(within(dlg).getByLabelText("Name"), "ERP");
    await u.click(within(dlg).getByLabelText(/Read and write/));
    await u.click(within(dlg).getByLabelText(/Include cost prices/));
    await u.selectOptions(within(dlg).getByLabelText("Expires after"), "Never");
    await u.click(within(dlg).getByRole("button", { name: "Create key" }));
    await screen.findByRole("dialog", { name: "Your new API key" });
    expect(posts[0].body).toEqual({ name: "ERP", scopes: ["read", "write", "costs:read"], expires_in_days: null });
  });

  it("limits a key to some things, with its own rate limit (API v2)", async () => {
    const u = userEvent.setup();
    render(<ApiKeysCard />);
    await screen.findByText("Zapier");
    await u.click(screen.getByRole("button", { name: /Create key/ }));
    const dlg = await screen.findByRole("dialog", { name: "Create API key" });
    await u.type(within(dlg).getByLabelText("Name"), "Website form");
    await u.click(within(dlg).getByLabelText(/Only some things/));
    await u.click(within(dlg).getByRole("button", { name: "Create key" }));
    expect(await within(dlg).findByText("Pick at least one thing this key can use.")).toBeInTheDocument();
    expect(posts).toHaveLength(0);
    await u.selectOptions(within(dlg).getByLabelText("Inquiries"), "write");
    await u.selectOptions(within(dlg).getByLabelText("Products"), "read");
    await u.type(within(dlg).getByLabelText("Requests a minute"), "60");
    await u.click(within(dlg).getByRole("button", { name: "Create key" }));
    await screen.findByRole("dialog", { name: "Your new API key" });
    expect(posts[0].body).toEqual({ name: "Website form", scopes: ["inquiries:write", "products:read"],
      expires_in_days: 365, rate_limit_per_min: 60 });
  });

  it("rotates from the menu and shows the new key once", async () => {
    const u = userEvent.setup();
    render(<ApiKeysCard />);
    await screen.findByText("Zapier");
    await u.click(screen.getByRole("button", { name: "More actions for Zapier" }));
    await u.click(await screen.findByRole("menuitem", { name: /Rotate/ }));
    const shown = await screen.findByRole("dialog", { name: "Your new API key" });
    expect(within(shown).getByTestId("fresh-key")).toHaveTextContent("rok_new_rotated");
    expect(within(shown).getByText(/old key keeps working for 24 hours/)).toBeInTheDocument();
    expect(posts[0].url).toContain("/api-keys/k1/rotate");
  });
});

describe("key text", () => {
  it("describes access in plain words", () => {
    expect(accessText({ scopes: "read" })).toBe("Read only");
    expect(accessText({ scopes: "read,write" })).toBe("Read and write");
    expect(accessText({ scopes: "*" })).toBe("Read and write");
    expect(accessText({ scopes: "costs:read,read" })).toBe("Read only, with cost prices");
    expect(lastUsedText(null)).toBe("Never used");
    expect(accessText({ scopes: "inquiries:write,products:read" })).toBe("Inquiries (read and change), Products (read)");
    expect(accessText({ scopes: "read,webhooks:write" })).toBe("Read only, Webhooks (read and change)");
    expect(limitText({ rate_limit_per_min: 30 })).toBe("30 requests a minute");
    expect(limitText({})).toBe("120 requests a minute");
  });

  it("builds scopes from the form", () => {
    expect(scopesFrom("read", {})).toEqual(["read"]);
    expect(scopesFrom("write", {}, true)).toEqual(["read", "write", "costs:read"]);
    expect(scopesFrom("some", { inquiries: "write", tags: "", products: "read" })).toEqual(["inquiries:write", "products:read"]);
    expect(scopesFrom("some", {}, true)).toEqual([]);
  });
});
