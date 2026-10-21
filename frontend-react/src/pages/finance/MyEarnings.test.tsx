// My earnings: totals, accrual status, the rule → job → line-item drill-down, the period
// picker, and the loading / empty / error states.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children, title }: any) => <div><h1>{title}</h1>{children}</div> }));
vi.mock("../../components/DateField", () => ({ default: ({ value, onChange, placeholder }: any) => <input aria-label={placeholder} value={value} onChange={(e) => onChange(e.target.value)} /> }));

import MyEarnings from "./MyEarnings";

const EARNINGS = {
  employee_id: "e1", name: "Tina", wage_type: "fixed", period_start: "2026-10-01", period_end: "2026-10-31", visible_from: null,
  base_salary: 1000, hourly_total: 0, commissions_total: 40, bonuses_total: 30, penalties_total: 10, total: 1060,
  accrual_status: "not_accrued", accruals: [],
  commissions: [
    { rule_id: "r1", rule_name: "Labour 10%", event: "labor_performed", calc_type: "percent", rate: 10, percent_basis: "total", active: true, total: 15,
      documents: [{ doc_type: "order", doc_id: "o1", number: "WO-00007", title: null, total: 15, items: [
        { line_id: "l1", name: "Screen swap", qty: 1, total: 10, entries: [{ id: "x1", date: "2026-10-03T10:00:00", amount: 10, kind: "earned" }] },
        { line_id: "l2", name: "Battery", qty: 1, total: 5, entries: [
          { id: "x2", date: "2026-10-03T10:00:00", amount: 5, kind: "earned" }, { id: "x3", date: "2026-10-04T10:00:00", amount: -5, kind: "reversed" },
          { id: "x4", date: "2026-10-05T10:00:00", amount: 5, kind: "earned" }] },
      ] }] },
    { rule_id: "r2", rule_name: "Close bonus", event: "order_closed", calc_type: "flat", rate: 25, percent_basis: null, active: true, total: 25,
      documents: [{ doc_type: "order", doc_id: "o1", number: "WO-00007", title: null, total: 25, items: [{ line_id: null, name: "Whole order", qty: null, total: 25, entries: [] }] }] },
  ],
  bonuses: [{ id: "b1", date: "2026-10-15T12:00:00", amount: 30, note: "Great month" }],
  penalties: [{ id: "p1", date: "2026-10-16T12:00:00", amount: 10, note: "Late" }],
};

let urls: string[] = [];
let respond: (url: string) => Response;
const reply = (status: number, body: unknown) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  urls = [];
  respond = () => reply(200, EARNINGS);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => { urls.push(url); return respond(url); }));
});
afterEach(() => vi.unstubAllGlobals());

const renderPage = () => render(<MemoryRouter><MyEarnings /></MemoryRouter>);

describe("My earnings", () => {
  it("shows the period total, the split and the accrual status", async () => {
    renderPage();
    expect(await screen.findByTestId("earnings-total")).toHaveTextContent("1,060");
    expect(screen.getByText("Not accrued yet")).toBeInTheDocument();
    expect(screen.getByText("Base salary")).toBeInTheDocument();
    expect(screen.getByText("Great month")).toBeInTheDocument();
    expect(screen.getByText("Late")).toBeInTheDocument();
    expect(urls[0]).toMatch(/^\/api\/payroll\/me\?from=\d{4}-\d{2}-01&to=\d{4}-\d{2}-\d{2}$/);
  });

  it("drills down from rule to job to line item", async () => {
    const u = userEvent.setup();
    renderPage();
    const rule = await screen.findByRole("button", { name: /Labour 10%/ });
    expect(rule).toHaveTextContent("10% of the total");
    expect(screen.queryByText("WO-00007")).not.toBeInTheDocument();
    await u.click(rule);
    const link = screen.getByRole("link", { name: /WO-00007/ });
    expect(link).toHaveAttribute("href", "/orders?open=o1");
    expect(screen.queryByText("Screen swap")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Show line items" }));
    expect(screen.getByText("Screen swap")).toBeInTheDocument();
    const battery = screen.getByText("Battery").closest("li")!;
    // a line that was earned, taken back and earned again lists every movement
    expect(within(battery).getAllByText(/Earned|Taken back/)).toHaveLength(3);
  });

  it("switching to last month reloads that period", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByTestId("earnings-total");
    await u.click(screen.getByRole("button", { name: "Last month" }));
    const now = new Date();
    const last = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    const ym = `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, "0")}`;
    await vi.waitFor(() => expect(urls.at(-1)).toContain(`from=${ym}-01`));
  });

  it("custom dates query exactly that range and flag a backwards range", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByTestId("earnings-total");
    await u.click(screen.getByRole("button", { name: "Custom" }));
    const from = screen.getByLabelText("From");
    await u.clear(from); await u.type(from, "2026-06-01");
    const to = screen.getByLabelText("To");
    await u.clear(to); await u.type(to, "2026-06-15");
    await vi.waitFor(() => expect(urls.at(-1)).toBe("/api/payroll/me?from=2026-06-01&to=2026-06-15"));
    await u.clear(from); await u.type(from, "2026-07-01");
    expect(await screen.findByText("The start date is after the end date.")).toBeInTheDocument();
  });

  it("shows empty states when nothing was earned", async () => {
    respond = () => reply(200, { ...EARNINGS, commissions: [], bonuses: [], penalties: [], commissions_total: 0, total: 0 });
    renderPage();
    expect(await screen.findByText("No commissions in this period yet.")).toBeInTheDocument();
    expect(screen.getByText("None in this period.")).toBeInTheDocument();
    expect(screen.getByText("Nothing accrued for this period yet.")).toBeInTheDocument();
  });

  it("explains a missing permission and can retry after an error", async () => {
    respond = () => reply(403, { detail: "Your role does not allow this action (can_view_own_payroll)" });
    const u = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("See their own earnings");
    respond = () => reply(200, EARNINGS);
    await u.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("earnings-total")).toHaveTextContent("1,060");
  });

  it("says when the role hides older pay", async () => {
    respond = () => reply(200, { ...EARNINGS, visible_from: "2026-10-10" });
    renderPage();
    expect(await screen.findByRole("note")).toHaveTextContent("Your role shows pay from");
  });
});
