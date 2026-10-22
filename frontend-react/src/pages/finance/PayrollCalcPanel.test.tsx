// Payroll calculation filters: every filter goes to the API, the export keeps them, and a row
// opens to the rule → job → line item breakdown.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
const opened: string[] = [];
vi.mock("../../api/client", async (orig) => {
  const real: any = await orig();
  return { ...real, openAuthedDoc: async (p: string) => { opened.push(p); } };
});

import PayrollCalcPanel, { amountTone, filterQuery } from "./PayrollCalcPanel";

let urls: string[] = [];
const reply = (status: number, body: unknown) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const ROWS = [
  { employee_id: "e1", name: "Ann", position: "Techs", base_salary: 100, commissions_total: 15, computed_total: 115 },
  { employee_id: "e2", name: "Ben", position: "Desk", base_salary: 200, computed_total: 200, accrual_status: "paid" },
];

beforeEach(() => {
  urls = []; opened.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    if (url.startsWith("/api/staff/locations")) return reply(200, { items: [{ id: "l1", name: "Main" }, { id: "l2", name: "Branch 2" }] });
    if (url.startsWith("/api/settings/rbac/roles")) return reply(200, { items: [{ id: "r1", name: "Techs" }, { id: "r2", name: "Desk" }] });
    if (url.startsWith("/api/payroll/calculation/detail")) return reply(200, { bonuses: [], penalties: [{ id: "p", amount: -10, note: "Late", date: "2026-10-02" }], breakdown: [
      { rule_id: "r", rule_name: "Labour 10%", event: "labor_performed", calc_type: "percent", rate: 10, percent_basis: "total", active: true, total: 15,
        documents: [{ doc_type: "order", doc_id: "o1", number: "WO-00009", title: null, total: 15, items: [] }] }] });
    if (url.startsWith("/api/payroll/calculation")) return reply(200, { items: url.includes("role_id=r2") ? [ROWS[1]] : ROWS });
    return reply(200, { items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const renderPanel = () => render(<MemoryRouter><PayrollCalcPanel month="2026-10" onMonth={() => {}} /></MemoryRouter>);

describe("Payroll calculation panel", () => {
  it("builds the query from the month and only the filters that are set", () => {
    expect(filterQuery("2026-10", { location_id: "", role_id: "r1", employee_id: "" })).toBe("month=2026-10&role_id=r1");
  });

  it("filters by location, role and employee and exports with the same filters", async () => {
    const u = userEvent.setup();
    renderPanel();
    await screen.findByRole("button", { name: "Show breakdown for Ann" });
    await u.selectOptions(await screen.findByLabelText("Location"), "l2");
    await u.selectOptions(screen.getByLabelText("Role"), "r2");
    await u.selectOptions(screen.getByLabelText("Employee"), "e2");
    await vi.waitFor(() => expect(urls.at(-1)).toBe("/api/payroll/calculation?month=2026-10&location_id=l2&role_id=r2&employee_id=e2"));
    expect(await screen.findByRole("button", { name: "Show breakdown for Ben" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show breakdown for Ann" })).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Export" }));
    expect(opened).toEqual(["/payroll/calculation/export.csv?month=2026-10&location_id=l2&role_id=r2&employee_id=e2"]);
    await u.click(screen.getByRole("button", { name: "Clear filters" }));
    await vi.waitFor(() => expect(urls.at(-1)).toBe("/api/payroll/calculation?month=2026-10"));
  });

  it("opens a row to the commission breakdown", async () => {
    const u = userEvent.setup();
    renderPanel();
    await u.click(await screen.findByRole("button", { name: "Show breakdown for Ann" }));
    expect(urls).toContain("/api/payroll/calculation/detail?employee_id=e1&month=2026-10");
    await u.click(await screen.findByRole("button", { name: /Labour 10%/ }));
    expect(screen.getByRole("link", { name: /WO-00009/ })).toBeInTheDocument();
    expect(screen.getByText("Late")).toBeInTheDocument();
  });

  it("says when nobody matches the filters", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => reply(200, { items: url.includes("role_id") ? [] : url.includes("rbac") ? [{ id: "r1", name: "Techs" }] : ROWS })));
    const u = userEvent.setup();
    renderPanel();
    await screen.findByRole("button", { name: "Show breakdown for Ann" });
    await u.selectOptions(screen.getByLabelText("Role"), "r1");
    expect(await screen.findByText("Nobody matches these filters.")).toBeInTheDocument();
  });

  it("keeps money columns neutral and colours them only when non-zero", async () => {
    expect(amountTone(0, "gain")).toBe("text-ink-tertiary");
    expect(amountTone(undefined, "loss")).toBe("text-ink-tertiary");
    expect(amountTone(15, "gain")).toBe("text-emerald-700");
    expect(amountTone(-10, "loss")).toBe("text-rose-600");
    const { container } = renderPanel();
    await screen.findByRole("button", { name: "Show breakdown for Ann" });
    const cell = (row: number, col: string) => container.querySelectorAll("tbody tr")[row].querySelector(`[data-col="${col}"]`)!;
    expect(cell(0, "commission").className).toContain("text-emerald-700");   // Ann earned commission
    expect(cell(1, "commission").className).toContain("text-ink-tertiary");  // Ben has none
    expect(cell(1, "commission").className).not.toContain("emerald");
    expect(cell(0, "penalty").className).toContain("text-ink-tertiary");
    expect(cell(0, "bonus").className).not.toContain("emerald");
  });
});
