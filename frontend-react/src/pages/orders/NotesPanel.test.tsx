// Comments and files: new comments start at the shop default, the poster can switch it, and a
// posted item can be flipped between private and shown to the customer.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import NotesPanel from "./NotesPanel";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let calls: { url: string; method: string; body: any }[] = [];
let feed: any[] = [];

beforeEach(() => {
  calls = [];
  feed = [{ id: "c1", item_type: "comment", body: "Back glass cracked", visibility: "private", author_name: "Sam", created_at: "2026-10-20T10:00:00" },
          { id: "s1", item_type: "system", body: "Status changed", visibility: "private", created_at: "2026-10-20T10:01:00" }];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url.includes("/staff/company")) return reply({ default_comment_visibility: "public", default_file_visibility: "private" });
    if (method === "PATCH") return reply({ ...feed[0], visibility: body.visibility });
    if (method === "POST") return reply({ id: "n", ...body });
    return reply({ items: feed });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("NotesPanel", () => {
  it("lists comments only, and new comments start at the shop default", async () => {
    const u = userEvent.setup();
    render(<NotesPanel orderId="o1" onFlash={vi.fn()} />);
    expect(await screen.findByText("Back glass cracked")).toBeTruthy();
    expect(screen.queryByText("Status changed")).toBeNull();      // system entries stay in history
    // shop default is public → the composer toggle reads "Customer sees"
    await vi.waitFor(() => expect(screen.getByRole("button", { name: /Shown to customer\. Make private/ })).toBeTruthy());
    await u.type(screen.getByPlaceholderText("Add a comment…"), "Ready Friday");
    await u.click(screen.getByRole("button", { name: "Post" }));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toBe("/api/feed/order/o1");
    expect(post.body).toMatchObject({ item_type: "comment", body: "Ready Friday", visibility: "client" });
  });

  it("the poster can switch a comment to private before posting", async () => {
    const u = userEvent.setup();
    render(<NotesPanel orderId="o1" onFlash={vi.fn()} />);
    const toggle = await screen.findByRole("button", { name: /Shown to customer\. Make private/ });
    await u.click(toggle);
    await u.type(screen.getByPlaceholderText("Add a comment…"), "Internal");
    await u.click(screen.getByRole("button", { name: "Post" }));
    expect(calls.find((c) => c.method === "POST")!.body.visibility).toBe("private");
  });

  it("flips a posted item to shown-to-customer", async () => {
    const u = userEvent.setup();
    render(<NotesPanel orderId="o1" onFlash={vi.fn()} />);
    await screen.findByText("Back glass cracked");
    // the row toggle (the composer's is the other one)
    const rowToggle = screen.getAllByRole("button", { name: /Private\. Show to customer/ })[0];
    await u.click(rowToggle);
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.url).toBe("/api/feed/item/c1");
    expect(patch.body).toEqual({ visibility: "client" });
  });
});
