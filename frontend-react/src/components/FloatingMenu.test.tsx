// The technician picker on a work-order line (and the print menu) open in a FloatingMenu:
// drawn on <body> so a card's overflow-hidden can't clip it, flipped above the button
// when there's no room below, closed by Escape or an outside click.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useState } from "react";
import FloatingMenu from "./FloatingMenu";

const rect = (top: number, height = 30) =>
  ({ top, bottom: top + height, left: 40, right: 160, width: 120, height, x: 40, y: top, toJSON: () => ({}) }) as DOMRect;

function Picker({ onPick }: { onPick: (n: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ overflow: "hidden" }}>
      <button data-pick="l1" onClick={() => setOpen((v) => !v)}>Priya Patel</button>
      {open && (
        <FloatingMenu anchorSel='[data-pick="l1"]' onClose={() => setOpen(false)}>
          {["Unassigned", "Rahul Sharma", "Priya Patel", "Amit Singh"].map((n) => (
            <button key={n} onClick={() => { onPick(n); setOpen(false); }}>{n}</button>
          ))}
        </FloatingMenu>
      )}
      <p>outside</p>
    </div>
  );
}

async function openAt(top: number, onPick = vi.fn()) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return this.dataset.pick ? rect(top) : rect(0, 0);
  });
  render(<Picker onPick={onPick} />);
  fireEvent.click(screen.getByRole("button", { name: "Priya Patel" }));
  await act(() => new Promise((r) => requestAnimationFrame(() => r(undefined))));
  return { menu: screen.getByRole("button", { name: "Amit Singh" }).parentElement as HTMLElement, onPick };
}

afterEach(() => vi.restoreAllMocks());

describe("FloatingMenu", () => {
  it("renders on <body>, outside the clipping card, below the button", async () => {
    const { menu } = await openAt(100);
    expect(menu.parentElement).toBe(document.body);
    expect(menu.style.position).toBe("fixed");
    expect(menu.style.top).toBe("134px");
  });

  it("flips above the button near the bottom of the window", async () => {
    const { menu } = await openAt(window.innerHeight - 40);
    expect(menu.style.top).toBe("");
    expect(menu.style.bottom).toBe("44px");
  });

  it("choosing someone reports the pick and closes", async () => {
    const { onPick } = await openAt(100);
    fireEvent.click(screen.getByRole("button", { name: "Amit Singh" }));
    expect(onPick).toHaveBeenCalledWith("Amit Singh");
    expect(screen.queryByRole("button", { name: "Amit Singh" })).toBeNull();
  });

  it("Escape closes it", async () => {
    await openAt(100);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("button", { name: "Amit Singh" })).toBeNull();
  });

  it("a click outside closes it", async () => {
    await openAt(100);
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(screen.queryByRole("button", { name: "Amit Singh" })).toBeNull();
  });

  it("follows the button when the page scrolls, and closes once it scrolls out of view", async () => {
    let top = 100;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return this.dataset.pick ? rect(top) : rect(0, 0);
    });
    render(<Picker onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Priya Patel" }));
    const frame = () => act(() => new Promise((r) => requestAnimationFrame(() => r(undefined))));
    await frame();
    const menu = screen.getByRole("button", { name: "Amit Singh" }).parentElement as HTMLElement;
    top = 60; fireEvent.scroll(window); await frame();
    expect(menu.style.top).toBe("94px");
    top = -200; fireEvent.scroll(window); await frame();
    expect(screen.queryByRole("button", { name: "Amit Singh" })).toBeNull();
  });

  it("arrow keys move between choices", async () => {
    await openAt(100);
    expect(document.activeElement).toBe(screen.getByRole("menu"));   // no ring on a choice after a click
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Unassigned" }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Rahul Sharma" }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Amit Singh" }));
  });
});
