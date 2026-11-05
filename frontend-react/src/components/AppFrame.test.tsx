// Persistent app frame (design polish C): every staff page sits inside the <AppFrame> layout route.
// The sidebar and header are drawn once and stay mounted while pages change; each page still names
// itself with <Shell title>, and open panels close on navigation as they did when Shell remounted.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, Link } from "react-router-dom";

vi.mock("../context/Me", async (orig) => {
  const real: any = await orig();
  return { ...real, useMe: () => ({ can: () => true, me: { name: "Asha Rao", email: "asha@example.com" }, refresh: vi.fn() }) };
});

import Shell, { AppFrame } from "./Shell";

const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(async () => reply({ items: [] })); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());

const PageA = () => <Shell title="Work orders"><p>Jobs list</p><Link to="/b">Go to B</Link></Shell>;
const PageB = () => <Shell title="Invoices"><p>Invoice list</p></Shell>;
const renderApp = () => render(
  <MemoryRouter initialEntries={["/a"]}>
    <Routes><Route element={<AppFrame />}><Route path="/a" element={<PageA />} /><Route path="/b" element={<PageB />} /></Route></Routes>
  </MemoryRouter>);
const unreadCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).includes("/tech/notifications")).length;

describe("AppFrame", () => {
  it("draws the frame once and names it after the page", () => {
    renderApp();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Work orders");
    expect(screen.getByText("Jobs list")).toBeInTheDocument();
  });

  it("keeps the same sidebar when the page changes, renames the header and refreshes the badges", async () => {
    const u = userEvent.setup();
    renderApp();
    const sidebar = screen.getAllByRole("navigation", { name: "Main menu" })[0];
    const before = unreadCalls();
    await u.click(screen.getByRole("link", { name: "Go to B" }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Invoices");
    expect(screen.getByText("Invoice list")).toBeInTheDocument();
    expect(screen.getAllByRole("navigation", { name: "Main menu" })[0]).toBe(sidebar);
    expect(unreadCalls()).toBe(before + 1);
  });

  it("closes the account menu when the page changes", async () => {
    const u = userEvent.setup();
    renderApp();
    await u.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByRole("button", { name: /Sign out/ })).toBeInTheDocument();
    await u.click(screen.getByRole("link", { name: "Go to B" }));
    expect(screen.queryByRole("button", { name: /Sign out/ })).not.toBeInTheDocument();
  });
});
