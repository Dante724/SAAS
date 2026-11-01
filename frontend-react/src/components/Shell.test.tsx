// Responsive shell (B3): below 900px the sidebar is replaced by a menu button that opens a
// slide-in nav drawer. jsdom has no layout, so the width itself is checked by
// e2e/responsive.spec.ts; this covers the drawer's behaviour and the class contract.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../context/Me", async (orig) => {
  const real: any = await orig();
  return { ...real, useMe: () => ({ can: () => true, me: { name: "Asha Rao", email: "asha@example.com" }, refresh: vi.fn() }) };
});

import Shell from "./Shell";

const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
beforeEach(() => { vi.stubGlobal("fetch", vi.fn(async () => reply({ items: [] }))); });
afterEach(() => vi.unstubAllGlobals());

const renderShell = () => render(<MemoryRouter><Shell title="Home"><p>Page body</p></Shell></MemoryRouter>);
const drawer = () => screen.getByRole("dialog", { name: "Main menu", hidden: true });

describe("Shell — narrow-screen navigation", () => {
  it("hides the desktop sidebar below 900px and shows a menu button there instead", () => {
    renderShell();
    const sidebar = screen.getAllByRole("navigation", { name: "Main menu" })[0].closest("aside")!;
    expect(sidebar.className).toMatch(/(^|\s)hidden(\s|$)/);
    expect(sidebar.className).toContain("min-[900px]:flex");
    expect(screen.getByRole("button", { name: "Open menu" }).className).toContain("min-[900px]:hidden");
  });

  it("opens the drawer from the menu button and closes it after picking a page", async () => {
    const u = userEvent.setup();
    renderShell();
    expect(drawer().closest("[aria-hidden]")).toHaveAttribute("aria-hidden", "true");
    await u.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "true");
    expect(drawer().closest("[aria-hidden]")).toHaveAttribute("aria-hidden", "false");
    await u.click(within(drawer()).getByRole("link", { name: /Work orders/ }));
    expect(drawer().closest("[aria-hidden]")).toHaveAttribute("aria-hidden", "true");
  });

  it("closes the drawer with Escape and with its close button", async () => {
    const u = userEvent.setup();
    renderShell();
    await u.click(screen.getByRole("button", { name: "Open menu" }));
    await u.keyboard("{Escape}");
    expect(drawer().closest("[aria-hidden]")).toHaveAttribute("aria-hidden", "true");
    await u.click(screen.getByRole("button", { name: "Open menu" }));
    await u.click(within(drawer()).getByRole("button", { name: "Close menu" }));
    expect(drawer().closest("[aria-hidden]")).toHaveAttribute("aria-hidden", "true");
  });
});
