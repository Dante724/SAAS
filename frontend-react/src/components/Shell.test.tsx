// Phones and iPad portrait: the sidebar is a drawer behind a menu button, not a fixed column
// that squeezes every page (launch audit C11).
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Shell from "./Shell";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

function setWidth(wide: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: wide, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn(async () => reply({ items: [] }))));
afterEach(() => vi.unstubAllGlobals());

const shell = () => render(<MemoryRouter><Shell title="Home"><p>page body</p></Shell></MemoryRouter>);

describe("Shell navigation", () => {
  it("on a narrow screen the menu is a drawer that opens and closes", async () => {
    setWidth(false);
    const u = userEvent.setup();
    shell();
    const nav = document.getElementById("app-nav")!;
    expect(nav.hasAttribute("inert")).toBe(true);            // closed: off screen and out of the tab order
    await u.click(screen.getByRole("button", { name: "Open menu" }));
    expect(nav.hasAttribute("inert")).toBe(false);
    await u.keyboard("{Escape}");
    expect(nav.hasAttribute("inert")).toBe(true);
  });

  it("on a desktop screen the sidebar stays put and there is no menu button", () => {
    setWidth(true);
    shell();
    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
    expect(document.getElementById("app-nav")!.hasAttribute("inert")).toBe(false);
  });
});
