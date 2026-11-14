// A5 — the track page shows the scheduled visit when the shop shares it.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import PublicTrack, { scheduleLines } from "./PublicTrack";

describe("scheduleLines", () => {
  it("is empty when the shop doesn't share a date", () => {
    expect(scheduleLines(null, null)).toEqual([]);
    expect(scheduleLines(undefined, "2026-10-21T12:00:00")).toEqual([]);
    expect(scheduleLines("not a date", null)).toEqual([]);
  });

  it("shows one line for a start time only", () => {
    const r = scheduleLines("2026-10-21T12:00:00", null);
    expect(r).toHaveLength(1);
    expect(r[0].label).toBe("Scheduled for");
  });

  it("shows one line with a time range on a single day", () => {
    const r = scheduleLines("2026-10-21T12:00:00", "2026-10-21T13:30:00");
    expect(r).toHaveLength(1);
    expect(r[0].label).toBe("Scheduled for");
    expect(r[0].value).toContain("–");
  });

  it("shows Start and End for a multi-day job", () => {
    const r = scheduleLines("2026-10-21T12:00:00", "2026-10-24T12:00:00");
    expect(r.map((x) => x.label)).toEqual(["Start", "End"]);
    expect(r[0].value).not.toEqual(r[1].value);
  });

  it("keeps a job ending at midnight on one day", () => {
    const r = scheduleLines("2026-10-21T09:00:00", "2026-10-22T00:00:00");
    expect(r.map((x) => x.label)).toEqual(["Scheduled for"]);
  });

  it("shows the shop's wall-clock time whatever the visitor's timezone", () => {
    // Stored 09:00 shop time must read 9:00 AM, never shifted by the browser's offset.
    const r = scheduleLines("2026-10-21T09:00:00", "2026-10-21T11:30:00");
    expect(r[0].value).toMatch(/Oct 21/);
    expect(r[0].value).toMatch(/9:00\s?AM/);
    expect(r[0].value).toMatch(/11:30\s?AM/);
  });

  it("ignores an end that isn't after the start", () => {
    expect(scheduleLines("2026-10-21T12:00:00", "2026-10-20T12:00:00").map((x) => x.label)).toEqual(["Scheduled for"]);
  });
});

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const BASE = { shop_name: "Fix Co", order_number: "WO-7", stage_label: "In repair", stages: [{ label: "Received" }, { label: "In repair" }], stage_index: 1, device: "iPhone 13" };

function renderTrack(doc: object) {
  vi.stubGlobal("fetch", vi.fn(async () => reply(doc)));
  window.history.pushState({}, "", "/track/tok");
  return render(<PublicTrack />);
}
afterEach(() => vi.unstubAllGlobals());

describe("PublicTrack details", () => {
  it("lists the scheduled day in Details", async () => {
    renderTrack({ ...BASE, scheduled_for: "2026-10-21T12:00:00", scheduled_to: "2026-10-21T14:00:00" });
    expect(await screen.findByText("Scheduled for")).toBeInTheDocument();
  });

  it("lists Start and End for several days", async () => {
    renderTrack({ ...BASE, scheduled_for: "2026-10-21T12:00:00", scheduled_to: "2026-10-23T12:00:00" });
    expect(await screen.findByText("Start")).toBeInTheDocument();
    expect(screen.getByText("End")).toBeInTheDocument();
  });

  it("shows nothing when the shop keeps the date private", async () => {
    renderTrack({ ...BASE, complaint: "Cracked", scheduled_for: null, scheduled_to: null });
    expect(await screen.findByText("Cracked")).toBeInTheDocument();
    expect(screen.queryByText("Scheduled for")).toBeNull();
  });
});

// E5 — estimate extras on the customer's page.
describe("PublicTrack estimate extras", () => {
  const EST = { ...BASE, is_estimate: true, awaiting_approval: true, can_choose_lines: true, total: 80,
    lines: [{ id: "l1", label: "Screen", qty: 1, amount: 50 }, { id: "l2", label: "Case", qty: 1, amount: 30 }] };
  function stub(doc: object) {
    const calls: { url: string; body: any }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
      return reply(url.endsWith("/respond") ? { ok: true } : doc);
    }));
    window.history.pushState({}, "", "/track/tok");
    render(<PublicTrack />);
    return calls;
  }

  it("lets the customer leave a line out and sends which", async () => {
    const { fireEvent } = await import("@testing-library/react");
    const calls = stub(EST);
    fireEvent.click(await screen.findByLabelText("Include Case"));
    expect(screen.getAllByText("$50").length).toBeGreaterThan(0);     // the total follows the ticks
    fireEvent.click(screen.getAllByText("Decline")[0]);
    fireEvent.change(screen.getByLabelText("Can you tell us why? (optional)"), { target: { value: "Too much" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Decline" }).at(-1)!);
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith("/respond"))).toBe(true));
    const sent = calls.find((c) => c.url.endsWith("/respond"))!.body;
    expect(sent.decision).toBe("decline");
    expect(sent.reason).toBe("Too much");
    expect(sent.skip_line_ids).toEqual([]);      // leaving lines out only applies to an approval
  });

  it("says when the estimate expired and offers no approval", async () => {
    stub({ ...EST, awaiting_approval: false, can_choose_lines: false, expired: true, valid_until: "2026-10-01" });
    expect(await screen.findByText(/This estimate expired on/)).toBeInTheDocument();
    expect(screen.queryByText("Review and sign")).toBeNull();
  });

  it("shows the deposit due at approval, and a pay button once approved", async () => {
    stub({ ...EST, deposit: { amount: 20, paid: 0, due: 20, can_pay: false } });
    expect((await screen.findAllByText(/A deposit of \$20 is due when you approve/)).length).toBeGreaterThan(0);
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
    stub({ ...BASE, is_estimate: false, deposit: { amount: 20, paid: 0, due: 20, can_pay: true } });
    expect((await screen.findAllByText(/Pay deposit/)).length).toBeGreaterThan(0);
  });
});

// Owner decision 8 — 90 days after the job closed the link answers 410 with the shop's contact.
describe("PublicTrack expired link", () => {
  const gone = (body: unknown) => ({ ok: false, status: 410, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

  it("shows a polite expired page with the shop's contact, never 'not found'", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => gone({
      detail: "This link has expired.", expired: true,
      shop: { name: "Fix Co", phone: "+1 555 0100", email: "hello@fixco.test" },
    })));
    window.history.pushState({}, "", "/track/old-tok");
    render(<PublicTrack />);
    expect(await screen.findByText("This link has expired")).toBeInTheDocument();
    expect(screen.getByText(/contact the shop/i)).toBeInTheDocument();
    expect(screen.getAllByText("Fix Co").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /\+1 555 0100/ })).toHaveAttribute("href", "tel:+1 555 0100");
    expect(screen.getByRole("link", { name: /hello@fixco\.test/ })).toHaveAttribute("href", "mailto:hello@fixco.test");
    expect(screen.queryByText("Order not found")).toBeNull();
  });

  it("still reads well when the shop has no phone or email", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => gone({ detail: "x", expired: true, shop: { name: "Fix Co", phone: null, email: null } })));
    window.history.pushState({}, "", "/track/old-tok");
    render(<PublicTrack />);
    expect(await screen.findByText("This link has expired")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^tel:/ })).toBeNull();
  });

  it("keeps 'not found' for a link that never existed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, headers: new Headers(), json: async () => ({ detail: "Order not found" }) }) as Response));
    window.history.pushState({}, "", "/track/bad");
    render(<PublicTrack />);
    expect(await screen.findByText("Order not found")).toBeInTheDocument();
  });
});
