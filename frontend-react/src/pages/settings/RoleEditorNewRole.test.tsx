// QA 31 Oct 2026 — a new role with no preset starts with nothing on (the server grants only
// what's sent), so the editor shows it that way and sends only what was switched on.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));

import { RoleEditor } from "./TeamSettings";

let bodies: any[] = [];
const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  bodies = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT" || init?.method === "POST") { bodies.push(JSON.parse(String(init.body))); return reply({}); }
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const catalog = { Workflows: [{ code: "can_create_tickets", label: "Create tickets" }, { code: "can_delete_tickets", label: "Delete tickets" }] };

describe("Role editor, new role", () => {
  it("starts with nothing on and saves only what was switched on", async () => {
    const u = userEvent.setup();
    const saved = vi.fn();
    render(<RoleEditor role={null} catalog={catalog} statuses={[]} onClose={() => {}} onSaved={saved} />);
    expect(screen.getByText("Can do 0 of 2 things")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is switched on yet/)).toBeInTheDocument();
    expect(screen.getByText("Works on jobs; doesn't take payments or give discounts")).toBeInTheDocument();
    await u.type(screen.getByPlaceholderText(/name/i), "Tech");
    await u.click(screen.getByRole("button", { name: /Workflows/ }));
    await u.click(screen.getByRole("switch", { name: /Create tickets/ }));
    expect(screen.getByText("Can do 1 of 2 things")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Create role" }));
    await vi.waitFor(() => expect(saved).toHaveBeenCalled());
    expect(bodies[0].permissions).toEqual({ can_create_tickets: true });
    expect(bodies[0].template).toBeUndefined();
  });
});
