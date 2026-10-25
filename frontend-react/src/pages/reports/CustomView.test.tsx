// Render test (code-review B): the COLUMNS picker must disappear and be replaced by a hint
// once a Group by is chosen — guards the UI half of the columns-vs-group-by fix against a
// future JSX edit that the buildReportConfig unit test wouldn't catch.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const get = vi.fn();
vi.mock("../../api/client", () => ({
  api: { get: (...a: any[]) => get(...a), post: vi.fn(), put: vi.fn(), del: vi.fn() },
}));
vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/DateField", () => ({ default: () => <input aria-label="date" /> }));
vi.mock("../../components/Pager", () => ({ default: () => null }));
vi.mock("../../components/SkeletonRows", () => ({ default: () => null }));

import { CustomView } from "./ReportsHub";

const SOURCES = {
  invoices: {
    label: "Invoices", date_field: "issued_at", has_location: false,
    fields: [
      { key: "invoice_number", label: "Invoice #", type: "text" },
      { key: "status", label: "Status", type: "text" },
      { key: "total", label: "Total", type: "money" },
    ],
  },
};

beforeEach(() => {
  get.mockReset();
  get.mockImplementation((path: string) => {
    if (path === "/custom-reports/sources") return Promise.resolve(SOURCES);
    if (path === "/custom-reports") return Promise.resolve({ items: [] });
    if (path === "/staff/locations") return Promise.resolve([]);
    return Promise.resolve({});
  });
});

describe("CustomView columns-vs-groupby", { timeout: 15000 }, () => {
  it("shows the Columns picker in flat mode, hides it + shows a hint under a Group by", async () => {
    const user = userEvent.setup();
    render(<CustomView />);

    // pick a source → flat mode: Columns picker appears with the field chips.
    // The select renders before /custom-reports/sources resolves, so wait for the option itself.
    await screen.findByRole("option", { name: "Invoices" }, { timeout: 5000 });
    const sourceSelect = screen.getByRole("combobox");
    await user.selectOptions(sourceSelect, "invoices");
    expect(screen.getByText("Columns")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Invoice #" })).toBeInTheDocument();

    // set a Group by → Columns picker is gone, replaced by the explanatory hint
    const groupBy = screen.getAllByRole("combobox").find((s) => within(s).queryByText("— none (list rows) —"))!;
    await user.selectOptions(groupBy, "status");

    expect(screen.queryByText("Columns")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invoice #" })).not.toBeInTheDocument();
    expect(screen.getByText(/set by/i)).toBeInTheDocument();
  });
});
