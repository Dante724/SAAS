// A selection never outlives the rows it was made on: searching, filtering or paging clears it.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("./WODrawer", () => ({ default: ({ order }: any) => <div data-testid="drawer">{order.id}</div> }));
vi.mock("./CreateOrderDrawer", () => ({ default: () => null }));
vi.mock("./JobLabels", () => ({ JobLabelsButton: ({ label }: any) => <button>{label}</button> }));

import WorkOrdersHub from "./WorkOrdersHub";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const rows = [
  { id: "o1", order_number: "WO-00001", status: "new", total: 10, client: { name: "Ann" } },
  { id: "o2", order_number: "WO-00002", status: "new", total: 20, client: { name: "Bo" } },
];

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/orders?")) return reply({ items: rows, total: 2, status_counts: { new: 2 }, assignees: [], open_jobs: 2 });
    if (url.includes("/statuses")) return reply({ items: [{ id: "s1", key: "new", name: "New" }] });
    if (url.includes("/filters/saved")) return reply({ items: [] });
    return reply({ items: {} });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("WorkOrdersHub selection", () => {
  it("clears the selection when the search changes", async () => {
    const u = userEvent.setup();
    render(<MemoryRouter><WorkOrdersHub /></MemoryRouter>);
    await u.click(await screen.findByRole("checkbox", { name: "Select WO-00001" }));
    expect(screen.getByRole("region", { name: "Selected work orders" })).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Selected work orders" })).getByText("1 selected")).toBeTruthy();
    await u.type(screen.getByRole("textbox", { name: "Search work orders" }), "Ann");
    expect(screen.queryByRole("region", { name: "Selected work orders" })).toBeNull();
  });

  it("shows status chips as a label with a quiet count", async () => {
    render(<MemoryRouter><WorkOrdersHub /></MemoryRouter>);
    const all = await screen.findByRole("button", { name: "All 2" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
  });
});
