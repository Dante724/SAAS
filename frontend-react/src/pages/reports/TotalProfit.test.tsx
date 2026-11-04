import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import TotalProfit from "./TotalProfit";

const get = vi.fn();
const openAuthedDoc = vi.fn();
vi.mock("../../api/client", () => ({
  api: { get: (...a: any[]) => get(...a) },
  openAuthedDoc: (...a: any[]) => openAuthedDoc(...a),
}));
vi.mock("../../lib/dialog", () => ({ alertDialog: vi.fn() }));

const b = (revenue: number, profit: number | undefined, count = 1) => ({
  count, revenue, tax: 0, cogs: profit === undefined ? undefined : revenue - profit, labour_cost: 0, commission: 0, profit, written_off: 0,
});

function report(costs = true) {
  const p = (v: number) => (costs ? v : undefined);
  const strip = (x: any) => { if (!costs) { delete x.profit; delete x.cogs; } return x; };
  const buckets = { jobs: strip(b(500, p(300), 3)), dropped: strip(b(30, p(20))), sales: strip(b(200, p(80), 2)), other: strip(b(-50, p(-40), 0)) };
  return {
    range: { from: "2026-10-01T00:00:00", to: "2026-10-31T23:59:59" },
    totals: strip({ ...b(680, p(360), 6), margin_pct: 52.9 }),
    buckets,
    previous: { ...strip(b(400, p(200))), buckets: { jobs: strip(b(300, p(150))), dropped: strip(b(0, p(0), 0)), sales: strip(b(100, p(50))), other: strip(b(0, p(0), 0)) },
                range: { from: "2026-09-01T00:00:00", to: "2026-09-30T23:59:59" } },
    by_location: [], by_technician: [],
    rows: [
      { kind: "job", id: "o1", number: "WO-1", date: "2026-10-05", revenue: 500, tax: 0, cogs: 200, labour_cost: 0, commission: 0, profit: 300, written_off: 0 },
      { kind: "other", id: null, number: null, date: "2026-10-31", revenue: -50, tax: 0, cogs: -10, labour_cost: 0, commission: 0, profit: -40, written_off: 0,
        reason: "Refunds, returns and credit notes in this period not inside a job above, and invoices that bill no job" },
    ],
  };
}

const parse = (s: string) => Number(s.replace(/[^\d.−-]/g, "").replace("−", "-"));

beforeEach(() => {
  get.mockReset(); openAuthedDoc.mockReset();
});

function mount(rep: any, locs = [{ id: "L1", name: "Main" }, { id: "L2", name: "Annex" }]) {
  get.mockImplementation((url: string) => (url.startsWith("/staff/employees")
    ? Promise.resolve({ items: [{ id: "E1", name: "Ana" }] })
    : Promise.resolve(rep)));
  return render(<MemoryRouter><TotalProfit locs={locs} /></MemoryRouter>);
}

describe("TotalProfit", () => {
  it("shows the Refunds and other card, and the cards add up to the total", async () => {
    mount(report());
    const other = await screen.findByTestId("profit-card-other");
    expect(within(other).getByText("Refunds and other")).toBeInTheDocument();
    const cards = ["jobs", "dropped", "sales", "other"].map((k) => parse(within(screen.getByTestId(`profit-card-${k}`)).getByTestId("card-amount").textContent || ""));
    const total = parse(screen.getByTestId("profit-total").textContent || "");
    expect(total).toBe(360);
    expect(cards.reduce((a, c) => a + c, 0)).toBe(total);
  });

  it("opens the refunds rows from the card", async () => {
    mount(report());
    fireEvent.click(await screen.findByTestId("profit-card-other"));
    const rows = await screen.findAllByText(/Refunds and other/);
    expect(rows.length).toBeGreaterThan(1);           // card title + drill title/row
    expect(screen.getByText("1 entry")).toBeInTheDocument();
  });

  it("with costs hidden the card sales add up to the total sales", async () => {
    mount(report(false));
    await screen.findByTestId("profit-card-other");
    const cards = ["jobs", "dropped", "sales", "other"].map((k) => parse(within(screen.getByTestId(`profit-card-${k}`)).getByTestId("card-amount").textContent || ""));
    expect(cards.reduce((a, c) => a + c, 0)).toBe(680);
    expect(screen.getByText(/Your role can't see costs, so profit is hidden/)).toBeInTheDocument();
  });

  it("hides the card when nothing reconciles", async () => {
    const rep = report();
    rep.buckets.other = b(0, 0, 0) as any;
    mount(rep);
    await screen.findByTestId("profit-card-jobs");
    expect(screen.queryByTestId("profit-card-other")).toBeNull();
  });

  it("labels the comparison period, says it's before running costs and when jobs count", async () => {
    mount(report());
    expect(await screen.findByText(/^vs /)).toBeInTheDocument();
    expect(screen.getByText(/Before running costs/)).toBeInTheDocument();
    expect(screen.getByText("Jobs closed, or invoiced and sent")).toBeInTheDocument();
    expect(screen.queryByText(/ex tax/i)).toBeNull();
    expect(screen.queryByText(/takings/i)).toBeNull();
  });

  it("exports the CSV with the same date, location and technician filters", async () => {
    openAuthedDoc.mockResolvedValue(undefined);
    mount(report());
    await screen.findByTestId("profit-card-jobs");
    fireEvent.change(screen.getByLabelText("Location"), { target: { value: "L2" } });
    await waitFor(() => expect(screen.getByText("Ana")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Technician"), { target: { value: "E1" } });
    await screen.findByTestId("profit-card-jobs");
    fireEvent.click(screen.getByText("Export"));
    await waitFor(() => expect(openAuthedDoc).toHaveBeenCalledTimes(1));
    const url: string = openAuthedDoc.mock.calls[0][0];
    expect(url.startsWith("/reports/total-profit.csv?")).toBe(true);
    const qs = new URLSearchParams(url.split("?")[1]);
    expect(qs.get("date_from")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(qs.get("date_to")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(qs.get("location_id")).toBe("L2");
    expect(qs.get("technician_id")).toBe("E1");
    // the page's own query uses the same filters
    const last = get.mock.calls.map((c) => c[0] as string).filter((u) => u.startsWith("/reports/total-profit?")).pop()!;
    expect(last.split("?")[1]).toBe(url.split("?")[1]);
  });
});
