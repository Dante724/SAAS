// WS-C inquiries: the drawer edits type / device / serial, shows the missed-call count and the
// activity timeline, sends only what changed, and converts to a job.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InquiryDrawer from "./InquiryDrawer";
import { TypesManager } from "./InquiriesHub";
import { callsLabel, changes, sla, utc, type Lead, type LeadType } from "./inquiries";

const types: LeadType[] = [{ id: "t1", name: "Repair", sla_minutes: 30, sort_order: 0, active: true },
  { id: "t2", name: "Old", sla_minutes: 60, sort_order: 1, active: false }];
const lead = (over: Partial<Lead> = {}): Lead => ({
  id: "l1", name: "Dana Reyes", phone: "+14155550100", source: "phone", status: "new", inquiry: "Missed call: call them back",
  call_count: 3, last_call_at: "2026-10-24T10:00:00", custom_data: {}, created_at: "2026-10-24T09:00:00",
  sla_due_at: new Date(Date.now() + 20 * 60000).toISOString(), ...over });

let calls: { method: string; url: string; body: any }[] = [];
function serve(route: (m: string, u: string, b: any) => unknown) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url: input, body });
    const res = route(method, input, body) ?? {};
    const err = res instanceof Error;
    const json = err ? { detail: (res as Error).message } : res;
    return { ok: !err, status: err ? 409 : 200, statusText: "", headers: new Headers(), json: async () => json, text: async () => JSON.stringify(json) } as Response;
  }));
}
const feed = { items: [{ id: "f1", item_type: "system", body: "Missed another call (3 in total)", created_at: "2026-10-24T10:00:00" },
  { id: "f2", item_type: "comment", body: "Left a voicemail", author_name: "Sam", created_at: "2026-10-24T10:05:00" }] };
beforeEach(() => serve((m, u) => (u.includes("/feed/lead/") ? feed : u.includes("/forms/entity-fields") ? { items: [] } : undefined)));
afterEach(() => vi.unstubAllGlobals());

describe("inquiry helpers", () => {
  it("labels missed calls and respond-by", () => {
    expect(callsLabel(lead({ call_count: 0 }))).toBeNull();
    expect(callsLabel(lead({ call_count: 1 }))).toBe("Missed call");
    expect(callsLabel(lead())).toBe("3 missed calls");
    expect(sla(lead({ status: "won" }))).toBeNull();
    expect(sla(lead({ sla_due_at: "2000-01-01T00:00:00" }))?.label).toBe("Overdue");
  });
  it("reads the server's zone-less times as UTC", () => {
    expect(utc("2026-10-25T06:46:00").toISOString()).toBe("2026-10-25T06:46:00.000Z");
    expect(utc("2026-10-25T06:46:00Z").toISOString()).toBe("2026-10-25T06:46:00.000Z");
    expect(utc("2026-10-25T12:16:00+05:30").toISOString()).toBe("2026-10-25T06:46:00.000Z");
    const due = new Date(Date.now() + 20 * 60000).toISOString().replace("Z", "");
    expect(sla(lead({ sla_due_at: due }))?.label).toMatch(/^(19|20)m left$/);
  });
  it("sends only changed fields, blanks as null", () => {
    expect(changes(lead(), lead())).toEqual({});
    expect(changes(lead(), lead({ serial: "", device_model: "iPhone 14" }))).toEqual({ device_model: "iPhone 14" });
    expect(changes(lead({ serial: "X" }), lead({ serial: "" }))).toEqual({ serial: null });
  });
});

describe("InquiryDrawer", () => {
  it("shows the missed-call count and the activity timeline", async () => {
    render(<InquiryDrawer lead={lead()} types={types} onClose={() => {}} onChanged={() => {}} onOpenOrder={() => {}} />);
    expect(screen.getByText(/3 missed calls/)).toBeInTheDocument();
    const act = screen.getByRole("region", { name: "Activity" });
    expect(await within(act).findByText("Left a voicemail")).toBeInTheDocument();
    expect(within(act).getByText(/Missed another call/)).toBeInTheDocument();
    // archived types aren't offered for a new pick
    expect(screen.queryByRole("option", { name: "Old" })).toBeNull();
  });

  it("saves only the device fields that changed", async () => {
    const u = userEvent.setup();
    const onChanged = vi.fn();
    serve((m, url, body) => {
      if (m === "PATCH") return { ...lead(), ...body };
      return url.includes("/feed/") ? { items: [] } : { items: [] };
    });
    render(<InquiryDrawer lead={lead()} types={types} onClose={() => {}} onChanged={onChanged} onOpenOrder={() => {}} />);
    await u.type(screen.getByLabelText("Model"), "iPhone 14");
    await u.selectOptions(screen.getByLabelText("Type"), "t1");
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.url).toBe("/api/leads/l1");
    expect(patch.body).toEqual({ device_model: "iPhone 14", lead_type_id: "t1" });
  });

  it("converts to a job and reports the job number", async () => {
    const u = userEvent.setup();
    const onChanged = vi.fn();
    serve((m, url) => (m === "POST" && url.includes("/convert") ? { ok: true, order_number: "WO-1042", order_id: "o1" } : { items: [] }));
    render(<InquiryDrawer lead={lead()} types={types} onClose={() => {}} onChanged={onChanged} onOpenOrder={() => {}} />);
    await u.click(screen.getByRole("button", { name: "Convert to job" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(null, "Job WO-1042 created"));
    expect(calls.some((c) => c.url === "/api/leads/l1/convert?is_estimate=false")).toBe(true);
  });

  it("shows the server's reason when converting fails", async () => {
    const u = userEvent.setup();
    serve((m, url) => (m === "POST" && url.includes("/convert") ? new Error("Add a location before converting inquiries") : { items: [] }));
    render(<InquiryDrawer lead={lead()} types={types} onClose={() => {}} onChanged={() => {}} onOpenOrder={() => {}} />);
    await u.click(screen.getByRole("button", { name: "Convert to job" }));
    expect((await screen.findAllByRole("alert")).some((a) => /Add a location/.test(a.textContent || ""))).toBe(true);
  });

  it("adds a note to the timeline", async () => {
    const u = userEvent.setup();
    render(<InquiryDrawer lead={lead()} types={types} onClose={() => {}} onChanged={() => {}} onOpenOrder={() => {}} />);
    await u.type(screen.getByLabelText("Add a note"), "Called back");
    await u.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/feed/lead/l1")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ item_type: "comment", body: "Called back", visibility: "private" });
  });

  it("a converted inquiry links to its job and has no actions", () => {
    const open = vi.fn();
    render(<InquiryDrawer lead={lead({ status: "won", converted_order_id: "o9", converted_order_number: "WO-9" })} types={types}
      onClose={() => {}} onChanged={() => {}} onOpenOrder={open} />);
    expect(screen.getByText(/Converted to WO-9/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Convert to job" })).toBeNull();
    screen.getByRole("button", { name: "Open job" }).click();
    expect(open).toHaveBeenCalledWith("o9");
  });
});

describe("TypesManager", () => {
  it("adds a type with its respond-by time", async () => {
    const u = userEvent.setup();
    const onChanged = vi.fn();
    render(<TypesManager types={types} onClose={() => {}} onChanged={onChanged} />);
    await u.type(screen.getByLabelText("New type"), "Sale");
    await u.clear(screen.getByLabelText("Respond within (min)"));
    await u.type(screen.getByLabelText("Respond within (min)"), "120");
    await u.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Sale", sla_minutes: 120 });
    expect(screen.getByText(/Old \(archived\)/)).toBeInTheDocument();
  });
});
