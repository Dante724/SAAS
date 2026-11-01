import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { clickable, clickableRow } from "./clickable";

describe("clickable", () => {
  it("makes a card a focusable button that Enter and Space activate", () => {
    const open = vi.fn();
    render(<div {...clickable(open)}>Card</div>);
    const el = screen.getByRole("button", { name: "Card" });
    expect(el).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(el, { key: "Enter" });
    fireEvent.keyDown(el, { key: " " });
    fireEvent.click(el);
    expect(open).toHaveBeenCalledTimes(3);
  });

  it("leaves keys pressed on a control inside the row to that control", () => {
    const open = vi.fn();
    render(<table><tbody><tr {...clickableRow(open)}><td><input aria-label="Select row" type="checkbox" /></td></tr></tbody></table>);
    fireEvent.keyDown(screen.getByLabelText("Select row"), { key: "Enter" });
    expect(open).not.toHaveBeenCalled();
    const row = screen.getByRole("row");
    expect(row).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(row, { key: "Enter" });
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("adds nothing when there is no handler", () => {
    expect(clickable(undefined)).toEqual({});
    expect(clickableRow(null)).toEqual({});
  });
});
