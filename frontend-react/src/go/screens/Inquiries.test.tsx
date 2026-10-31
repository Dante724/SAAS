// The Inquiries screen: the open list (late first), a missed-call link opening one inquiry, and
// Convert to job landing on the new job.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";

vi.mock("../../context/Me", () => ({ useMe: () => ({ me: { id: "e1", name: "Sam" }, can: () => true }) }));
vi.mock("../lib/offlineQueue", () => ({ goQueue: () => ({ subscribe: () => () => {}, mutate: vi.fn(async () => ({ ok: true, queued: false, data: {} })) }) }));
import Inquiries from "./Inquiries";

const reply = (body: unknown, status = 200) =>
  ({ ok: status < 400, status, headers: new Headers({ "Content-Type": "application/json" }), json: async () => body, text: async () => JSON.stringify(body), clone() { return this; } }) as unknown as Response;

const LIST = {
  total: 2,
  items: [
    { id: "a", name: "Tom Reed", status: "in_progress", phone: "0400 111 222", lead_type: "Quote", device_brand: "Samsung", device_model: "S23", sla_due_at: "2099-01-01T10:00:00" },
    { id: "b", name: "Maria Lopez", status: "new", phone: "0412 345 678", device_brand: "Apple", device_model: "iPhone 14", serial: "F2LX", call_count: 3, sla_due_at: "2020-01-01T10:00:00", sla_overdue: true },
  ],
};

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/convert")) return reply({ order_id: "o-7", order_number: "WO-0042" });
    if (url.startsWith("/api/leads?")) return reply(LIST);
    if (url === "/api/leads/b") return reply(LIST.items[1]);
    return reply({});
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function JobStub() { const { id } = useParams(); return <p>job {id}</p>; }
const wait = { timeout: 5000 };   // the full suite runs in parallel; give the first fetch time
const app = (at = "/inquiries") => render(
  <MemoryRouter initialEntries={[at]}>
    <Routes>
      <Route path="/inquiries" element={<Inquiries />} />
      <Route path="/job/:id" element={<JobStub />} />
    </Routes>
  </MemoryRouter>,
);

describe("Inquiries screen", () => {
  it("lists open inquiries, late first, with missed calls", async () => {
    app();
    const rows = await screen.findAllByRole("button", { name: /Tom Reed|Maria Lopez/ }, wait);
    expect(rows.map((r) => r.textContent)[0]).toMatch(/Maria Lopez.*Late/);
    expect(rows[0]).toHaveTextContent("3 missed calls");
    expect(rows[1]).toHaveTextContent("Quote · Samsung S23");
    expect(screen.getByText(/2 open/)).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/leads?open_only=true&page=1&page_size=25");
  });

  it("a missed-call link opens that inquiry", async () => {
    app("/inquiries?lead=b");
    const sheet = await screen.findByRole("dialog", { name: "Maria Lopez" }, wait);
    expect(within(sheet).getByRole("link", { name: /Call 0412 345 678/ })).toHaveAttribute("href", "tel:0412345678");
    expect(within(sheet).getByText("F2LX")).toBeInTheDocument();
  });

  it("Convert to job makes the job and opens it", async () => {
    const u = userEvent.setup();
    app();
    await u.click(await screen.findByRole("button", { name: /Maria Lopez/ }, wait));
    await u.click(await screen.findByRole("button", { name: /Convert to job/ }));
    expect(await screen.findByText("job o-7", {}, wait)).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/convert"))!;
    expect(call[0]).toBe("/api/leads/b/convert?is_estimate=false");
    expect(call[1].method).toBe("POST");
  });
});
