import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ApiKeysCard, { describeScopes, scopesFrom } from "./ApiKeysCard";

const Card = ({ title, children }: any) => <section aria-label={title}>{children}</section>;
let posts: any[] = [];
function serve() {
  posts = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") { posts.push(JSON.parse(String(init.body))); return { ok: true, status: 201, headers: new Headers(), json: async () => ({ key: "rok_abc_secret" }), text: async () => "" } as any; }
    return { ok: true, status: 200, headers: new Headers(), json: async () => ({ items: [{ id: "k1", name: "Site", prefix: "rok_1", scopes: "inquiries:write,products:read", active: true, rate_limit_per_min: 30 }] }), text: async () => "" } as any;
  }));
}
afterEach(() => vi.unstubAllGlobals());

describe("API key scopes", () => {
  it("turns the form into scopes and back into words", () => {
    expect(scopesFrom("all", {})).toBe("read,write");
    expect(scopesFrom("read", {})).toBe("read");
    expect(scopesFrom("some", { inquiries: "write", products: "read", tags: "" })).toBe("inquiries:write,products:read");
    expect(describeScopes("inquiries:write,products:read")).toBe("Inquiries (change), Products (read)");
    expect(describeScopes("read,write")).toBe("Everything");
    expect(describeScopes("read")).toBe("Read everything");
  });

  it("creates a key limited to inquiries, with its own limit, and shows the key once", async () => {
    serve();
    const u = userEvent.setup();
    render(<ApiKeysCard Card={Card} />);
    expect(await screen.findByText("Inquiries (change), Products (read)")).toBeInTheDocument();
    expect(screen.getByText("30/min")).toBeInTheDocument();
    await u.type(screen.getByLabelText("New key name"), "Website form");
    await u.selectOptions(screen.getByLabelText("What it can do"), "some");
    expect(screen.getByRole("button", { name: "Create key" })).toBeDisabled();
    await u.selectOptions(screen.getByLabelText("Inquiries"), "write");
    await u.type(screen.getByLabelText("Requests a minute"), "60");
    await u.click(screen.getByRole("button", { name: "Create key" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ name: "Website form", scopes: "inquiries:write", rate_limit_per_min: 60 });
    expect(await screen.findByText("rok_abc_secret")).toBeInTheDocument();
  });
});
