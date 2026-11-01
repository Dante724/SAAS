// G3 / J2 / J3: the job's customer messages, notes and passcode tell the truth about what happened.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import ConversationPanel from "./ConversationPanel";
import { DevicePasscode, JobNotes } from "./WODrawer";

type Call = { url: string; method: string; body: any };
let calls: Call[] = [];
let routes: Record<string, (body: any) => unknown> = {};
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  calls = []; routes = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const h = routes[`${method} ${url}`];
    if (!h) return reply({ items: [] });
    const out = h(body) as any;
    return out && out.__status ? reply(out.body, out.__status) : reply(out);
  }));
});
afterEach(() => vi.unstubAllGlobals());

const posted = (path: string) => calls.filter((c) => c.method !== "GET" && c.url === `/api${path}`);

describe("ConversationPanel", () => {
  it("shows the server's 'not sent' sentence instead of claiming the message was sent", async () => {
    const msg = "Saved on the job, but not sent: set up email or text messages in Settings → Integrations.";
    routes["POST /api/orders/o1/thread"] = (b) => ({ id: "m1", from: "staff", who: "Sam", body: b.body, at: "2026-10-30T10:00:00Z", delivery: { status: "not_set_up", channel: null, message: msg } });
    const onFlash = vi.fn();
    const u = userEvent.setup();
    render(<ConversationPanel orderId="o1" onFlash={onFlash} />);
    await u.type(screen.getByLabelText("Message the customer"), "Your phone is ready");
    await u.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("status")).toHaveTextContent(msg);
    expect(onFlash).toHaveBeenCalledWith(msg, "warn");
    expect(screen.queryByText(/message sent/i)).toBeNull();
    expect(posted("/orders/o1/thread")[0].body).toEqual({ body: "Your phone is ready", channel: "auto" });
  });

  it("sends by the picked channel and shows a real 'sent' result plainly", async () => {
    routes["POST /api/orders/o1/thread"] = () => ({ id: "m1", from: "staff", body: "x", delivery: { status: "sent", channel: "sms", message: "Sent by text to the customer." } });
    const onFlash = vi.fn();
    const u = userEvent.setup();
    render(<ConversationPanel orderId="o1" onFlash={onFlash} />);
    await u.click(screen.getByRole("radio", { name: "Text" }));
    await u.type(screen.getByLabelText("Message the customer"), "Hi");
    await u.click(screen.getByRole("button", { name: "Send message" }));
    await vi.waitFor(() => expect(onFlash).toHaveBeenCalledWith("Sent by text to the customer.", undefined));
    expect(posted("/orders/o1/thread")[0].body.channel).toBe("sms");
  });

  it("shows the reason when the user isn't allowed to message customers", async () => {
    routes["POST /api/orders/o1/thread"] = () => ({ __status: 403, body: { detail: "You don't have permission to message customers" } });
    const u = userEvent.setup();
    render(<ConversationPanel orderId="o1" />);
    await u.type(screen.getByLabelText("Message the customer"), "Hi");
    await u.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("status")).toHaveTextContent("You don't have permission to message customers");
    expect(screen.getByLabelText("Message the customer")).toHaveValue("Hi");
  });
});

describe("JobNotes", () => {
  it("adds notes for staff only by default, and for the customer when switched", async () => {
    routes["GET /api/feed/order/o1"] = () => ({ items: [{ id: "f1", item_type: "comment", body: "Screen ordered", visibility: "client", author_name: "Sam", created_at: "2026-10-30T10:00:00Z" }] });
    routes["POST /api/feed/order/o1"] = () => ({ id: "f2" });
    const u = userEvent.setup();
    render(<JobNotes orderId="o1" filter="All" onFlash={vi.fn()} />);
    expect(await screen.findByText("Screen ordered")).toBeInTheDocument();
    expect(screen.getByText("Customer can see", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Only staff/ })).toHaveAttribute("aria-checked", "true");

    await u.type(screen.getByLabelText("Add a note"), "Internal");
    await u.click(screen.getByRole("button", { name: "Add note" }));
    await vi.waitFor(() => expect(posted("/feed/order/o1")).toHaveLength(1));
    expect(posted("/feed/order/o1")[0].body).toMatchObject({ item_type: "comment", body: "Internal", visibility: "private" });

    await u.click(screen.getByRole("radio", { name: /Customer can see/ }));
    await u.type(screen.getByLabelText("Add a note"), "Ready Friday");
    await u.click(screen.getByRole("button", { name: "Add note" }));
    await vi.waitFor(() => expect(posted("/feed/order/o1")).toHaveLength(2));
    expect(posted("/feed/order/o1")[1].body).toMatchObject({ body: "Ready Friday", visibility: "client" });
  });
});

describe("DevicePasscode", () => {
  it("hides the passcode until Show, then hides it again", async () => {
    routes["GET /api/assets/a1/passcode"] = () => ({ passcode: "1234" });
    const u = userEvent.setup();
    render(<DevicePasscode assetId="a1" hasPasscode onFlash={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByText("••••")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/passcode"))).toBe(false);
    await u.click(screen.getByRole("button", { name: "Show" }));
    expect(await screen.findByText("1234")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByText("1234")).toBeNull();
  });

  it("saves a new passcode", async () => {
    routes["PUT /api/assets/a1/passcode"] = () => ({ ok: true, has_passcode: true });
    const onChanged = vi.fn();
    const u = userEvent.setup();
    render(<DevicePasscode assetId="a1" hasPasscode={false} onFlash={vi.fn()} onChanged={onChanged} />);
    await u.click(screen.getByRole("button", { name: /Add device passcode/ }));
    await u.type(screen.getByLabelText("Device passcode"), "0000");
    await u.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledWith(true));
    expect(posted("/assets/a1/passcode")[0].body).toEqual({ passcode: "0000" });
  });
});
