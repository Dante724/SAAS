// Stock list look: margin reads in ink and turns red only when a part sells below cost.
import { describe, it, expect, vi } from "vitest";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import { marginOf } from "./InventoryHub";
import { productGlyph, stockChip } from "../sales/SalesPOS";

describe("marginOf", () => {
  it("uses the server's margin when it has one", () => {
    expect(marginOf({ margin_pct: 42, sell_price: 10, cost_price: 9 })).toBe(42);
  });
  it("works it out from sell and cost, negative when selling below cost", () => {
    expect(marginOf({ sell_price: 100, cost_price: 60 })).toBe(40);
    expect(marginOf({ sell_price: 50, cost_price: 60 })).toBe(-20);
  });
  it("has no margin without a sell price", () => {
    expect(marginOf({ sell_price: 0, cost_price: 5 })).toBeNull();
  });
});

describe("product tiles", () => {
  it("draw a neutral glyph from the name, never a coloured code chip", () => {
    expect(productGlyph("iPhone 13 screen")).toBe("smartphone");
    expect(productGlyph("MacBook Air battery")).toBe("laptop");
    expect(productGlyph("SIM card tray")).toBe("inventory_2");
    expect(productGlyph("Thermal paste")).toBe("inventory_2");
  });
  it("flag stock only when it is low or gone", () => {
    expect(stockChip(12, false)).toEqual({ label: "12 in stock", warn: false });
    expect(stockChip(2, true)).toEqual({ label: "2 left", warn: true });
    expect(stockChip(0, false)).toEqual({ label: "Out of stock", warn: true });
  });
});

describe("stock list when the load fails (launch review B9)", () => {
  it("says the stock couldn't load and offers a retry, never 'No stock yet'", async () => {
    const { render, screen, fireEvent } = await import("@testing-library/react");
    const { StockLoadError } = await import("./InventoryHub");
    const retry = vi.fn();
    render(<StockLoadError onRetry={retry} />);
    expect(screen.getByRole("alert").textContent).toContain("Couldn't load your stock");
    expect(screen.queryByText("No stock yet")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
