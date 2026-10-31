// Catalog picker: browsing by collapsible category or as one list, with stock and category shown.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("./Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
import CatalogPicker from "./CatalogPicker";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const items = [
  { kind: "product", ref_id: "p1", name: "iPhone 14 Pro Screen", category: "Screens", price: 85, available: 8, sku: "SCRN-1" },
  { kind: "product", ref_id: "p2", name: "S23 Battery", category: "Batteries", price: 18, available: 1 },
  { kind: "product", ref_id: "p3", name: "Thermal Paste", category: null, price: 2, available: 0 },
];

beforeEach(() => {
  try { localStorage.removeItem("ros_catalog_view"); } catch { /* ignore */ }
  vi.stubGlobal("fetch", vi.fn(async () => reply({ items })));
});
afterEach(() => vi.unstubAllGlobals());

describe("CatalogPicker browsing", () => {
  it("starts with categories collapsed, opens one, and can expand all", async () => {
    render(<CatalogPicker type="product" onPick={() => {}} />);
    await userEvent.click(screen.getByRole("combobox"));
    const screens = await screen.findByRole("button", { name: /Screens/ });
    expect(screens).toHaveAttribute("aria-expanded", "false");
    // Uncategorised items come last.
    const heads = screen.getAllByRole("button", { expanded: false }).map((b) => b.textContent);
    expect(heads.at(-1)).toMatch(/Other products/);
    expect(screen.queryByText("iPhone 14 Pro Screen")).toBeNull();

    await userEvent.click(screens);
    expect(screen.getByText("iPhone 14 Pro Screen")).toBeInTheDocument();
    expect(screen.getByText("8 in stock")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getByText("Only 1 left")).toBeInTheDocument();
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
  });

  it("All items shows one A–Z list with the category under each name, and is remembered", async () => {
    const { unmount } = render(<CatalogPicker type="product" onPick={() => {}} />);
    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(await screen.findByRole("tab", { name: "All items" }));
    const names = screen.getAllByRole("option").map((o) => o.textContent || "");
    expect(names[0]).toMatch(/^iPhone 14 Pro Screen.*Screens · SCRN-1/);
    expect(names[1]).toMatch(/^S23 Battery.*Batteries/);
    expect(screen.queryByRole("button", { name: /Screens/ })).toBeNull();
    unmount();

    render(<CatalogPicker type="product" onPick={() => {}} />);
    await userEvent.click(screen.getByRole("combobox"));
    expect(await screen.findByRole("tab", { name: "All items" })).toHaveAttribute("aria-selected", "true");
  });
});
