// Chats inbox: status tabs with counts, and moving a conversation to / out of Spam.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import ChatsHub, { threadsUrl } from "./ChatsHub";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const counts = { all: 2, new: 1, reply_needed: 1, replied: 0, completed: 0, spam: 1 };
const ana = { id: "t1", channel: "sms", contact_phone: "+1555", contact_name: "Ana", status: "new", is_spam: false, last_body: "hi" };
const ben = { id: "t2", channel: "whatsapp", contact_phone: "+1666", contact_name: "Ben", status: "reply_needed", is_spam: false, last_body: "when?" };
const junk = { id: "t3", channel: "sms", contact_phone: "+1999", contact_name: "Promo", status: "new", is_spam: true, last_body: "BUY" };

let calls: { method: string; url: string; body?: any }[] = [];
let failList = false;
let replied = false;

beforeEach(() => {
  calls = []; failList = false; replied = false;
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (method === "POST" && url.endsWith("/send")) { replied = true; return reply({ id: "m2", status: "sent", thread_status: "replied" }); }
    if (method === "PATCH") {
      const b = JSON.parse(String(init!.body));
      return reply({ ...ana, ...b, spam_at: b.is_spam ? "2026-10-21T10:00:00" : null });
    }
    if (url.includes("/messages")) return reply({ items: [{ id: "m1", direction: "in", body: "hi", status: "received" }] });
    if (url.startsWith("/api/chats/threads")) {
      if (failList) return reply({ detail: "boom" }, 500);
      if (url.includes("folder=spam")) return reply({ items: [junk], counts });
      if (url.includes("status=reply_needed")) return reply({ items: [ben], counts });
      if (url.includes("status=new")) return reply({ items: replied ? [] : [ana], counts });
      return reply({ items: [ana, ben], counts });
    }
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("ChatsHub status tabs", () => {
  it("builds the list URL for a status tab and for the Spam folder", () => {
    expect(threadsUrl("all")).toBe("/chats/threads?status=all");
    expect(threadsUrl("reply_needed")).toBe("/chats/threads?status=reply_needed");
    expect(threadsUrl("spam")).toBe("/chats/threads?folder=spam");
  });

  it("shows each tab with its count and filters by status", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    const tabs = await screen.findByRole("tablist", { name: "Chat status" });
    expect(within(tabs).getByRole("tab", { name: /All\s*2/ })).toHaveAttribute("aria-selected", "true");
    expect(within(tabs).getByRole("tab", { name: /Reply needed\s*1/ })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: /Spam\s*1/ })).toBeInTheDocument();
    await u.click(within(tabs).getByRole("tab", { name: /Reply needed/ }));
    expect(calls.some((c) => c.url === "/api/chats/threads?status=reply_needed")).toBe(true);
    expect(await screen.findAllByText("Ben")).not.toHaveLength(0);
    expect(screen.queryByText("Ana")).toBeNull();
  });

  it("shows an error with a retry when the list fails to load", async () => {
    failList = true;
    const u = userEvent.setup();
    render(<ChatsHub />);
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
    failList = false;
    await u.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByText("Ana")).not.toHaveLength(0);
  });
});

describe("ChatsHub spam", () => {
  it("marks the open conversation as spam and removes it from the inbox list", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    await u.click(await screen.findByRole("button", { name: "More actions" }));
    await u.click(await screen.findByRole("button", { name: /Mark as spam/ }));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch).toMatchObject({ url: "/api/chats/threads/t1", body: { is_spam: true } });
    expect(await screen.findByText("Moved to spam")).toBeInTheDocument();
    expect(await screen.findByText(/Pick a conversation/)).toBeInTheDocument();
  });

  it("restores a conversation from the Spam folder", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    await u.click(await screen.findByRole("tab", { name: /Spam/ }));
    expect(await screen.findAllByText("Promo")).not.toHaveLength(0);   // first chat opens on desktop
    await u.click(await screen.findByRole("button", { name: /Not spam/ }));
    expect(calls.find((c) => c.method === "PATCH")).toMatchObject({ url: "/api/chats/threads/t3", body: { is_spam: false } });
    expect(await screen.findByText("Moved back to the inbox")).toBeInTheDocument();
  });

  it("changes the status from the conversation's more menu", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    await u.click(await screen.findByRole("button", { name: "More actions" }));
    await u.selectOptions(await screen.findByRole("combobox", { name: "Chat status" }), "completed");
    expect(calls.find((c) => c.method === "PATCH")).toMatchObject({ body: { status: "completed" } });
    expect(await screen.findByText("Marked as completed")).toBeInTheDocument();
  });

  it("closes the more menu with Escape", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    const more = await screen.findByRole("button", { name: "More actions" });
    await u.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    await u.keyboard("{Escape}");
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /Mark as spam/ })).toBeNull();
  });

  it("shows the new status in the header after a reply, even when the chat leaves the tab", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    await u.click(await screen.findByRole("tab", { name: /^New/ }));
    await u.click(await screen.findByRole("button", { name: "More actions" }));
    expect(await screen.findByRole("combobox", { name: "Chat status" })).toHaveValue("new");
    await u.type(screen.getByPlaceholderText("Message…"), "On it{Enter}");
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/send"))).toBe(true));
    await u.click(await screen.findByRole("button", { name: "More actions" }));
    await vi.waitFor(() => expect(screen.getByRole("combobox", { name: "Chat status" })).toHaveValue("replied"));
  });
});

describe("ChatsHub polish", () => {
  it("completes the open conversation in one click", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    await u.click(await screen.findByRole("button", { name: /^Complete$/ }));
    expect(calls.find((c) => c.method === "PATCH")).toMatchObject({ url: "/api/chats/threads/t1", body: { status: "completed" } });
    expect(await screen.findByText("Marked as completed")).toBeInTheDocument();
  });

  it("puts a quick reply into the composer instead of sending it", async () => {
    const u = userEvent.setup();
    render(<ChatsHub />);
    const input = await screen.findByPlaceholderText("Message…");
    await u.type(input, "Hi Ana,");
    await u.click(screen.getByRole("button", { name: /ready for pickup/ }));
    expect(input).toHaveValue("Hi Ana, It's ready for pickup!");
    expect(calls.some((c) => c.url.endsWith("/send"))).toBe(false);
  });

  it("hides zero counts on the status tabs", async () => {
    render(<ChatsHub />);
    const tabs = await screen.findByRole("tablist", { name: "Chat status" });
    await screen.findAllByText("Ana");
    expect(within(tabs).getByRole("tab", { name: "Replied" })).toBeInTheDocument();
    expect(within(tabs).getByRole("tab", { name: "Completed" })).toBeInTheDocument();
    expect(within(tabs).queryByText("0")).toBeNull();
  });
});

// B10: switching chats fast must never show one customer's messages under another's name.
describe("ChatsHub switching conversations", () => {
  it("drops a late reply for the chat you just left", async () => {
    let releaseAna!: () => void;
    const anaHeld = new Promise<void>((r) => { releaseAna = r; });
    const base = (globalThis.fetch as any).getMockImplementation();
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/threads/t1/messages")) {
        await anaHeld;   // Ana's messages arrive after we've already moved to Ben
        return reply({ items: [{ id: "a1", direction: "in", body: "Alice's private message", status: "received" }] });
      }
      if (url.endsWith("/threads/t2/messages")) return reply({ items: [{ id: "b1", direction: "in", body: "Ben asks about his laptop", status: "received" }] });
      return base(url, init);
    }));
    const u = userEvent.setup();
    render(<ChatsHub />);
    // the first chat (Ana) opens on desktop and its messages are still loading; pick Ben
    const list = await screen.findAllByRole("button", { name: /Ben/ });
    await u.click(list[0]);
    expect(await screen.findByText("Ben asks about his laptop")).toBeInTheDocument();
    releaseAna();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Alice's private message")).toBeNull();
    expect(screen.getByText("Ben asks about his laptop")).toBeInTheDocument();
  });

  it("never shows the last chat's messages while the next one loads", async () => {
    const base = (globalThis.fetch as any).getMockImplementation();
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/threads/t1/messages")) return reply({ items: [{ id: "a1", direction: "in", body: "Alice's private message", status: "received" }] });
      if (url.endsWith("/threads/t2/messages")) return new Promise<Response>(() => {});   // Ben's never come back
      return base(url, init);
    }));
    const u = userEvent.setup();
    render(<ChatsHub />);
    expect(await screen.findByText("Alice's private message")).toBeInTheDocument();
    await u.click((await screen.findAllByRole("button", { name: /Ben/ }))[0]);
    expect(screen.queryByText("Alice's private message")).toBeNull();
  });
});
