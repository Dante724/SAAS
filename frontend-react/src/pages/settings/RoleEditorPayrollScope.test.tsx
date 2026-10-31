// E3 — the role editor's "Whose pay" control: shows the saved scope, needs people when limited
// to some, and saves scope + people.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));

import { RoleEditor } from "./TeamSettings";

let bodies: any[] = [];
const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  bodies = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT" || init?.method === "POST") { bodies.push(JSON.parse(String(init.body))); return reply({}); }
    if (url.startsWith("/api/staff/employees")) return reply([{ id: "e1", name: "Ann" }, { id: "e2", name: "Ben" }]);
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const role = { id: "r1", name: "Payroll", permissions: {}, perms_explicit: true, payroll_scope: "all", payroll_employee_ids: [] };

describe("Role editor payroll scope", () => {
  it("limits payroll to chosen people and saves them", async () => {
    const u = userEvent.setup();
    const saved = vi.fn();
    render(<RoleEditor role={role} catalog={{}} statuses={[]} onClose={() => {}} onSaved={saved} />);
    await u.click(screen.getByRole("button", { name: "What they see" }));
    expect(screen.getByRole("button", { name: "Everyone" })).toHaveAttribute("aria-pressed", "true");
    await u.click(screen.getByRole("button", { name: "Only some people" }));
    await u.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Pick at least one person/)).toBeInTheDocument();
    expect(bodies).toHaveLength(0);

    await u.click(await screen.findByRole("button", { name: "Ben" }));
    await u.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(saved).toHaveBeenCalled());
    expect(bodies[0]).toMatchObject({ payroll_scope: "selected", payroll_employee_ids: ["e2"] });
  });

  it("sends no people for own scope", async () => {
    const u = userEvent.setup();
    render(<RoleEditor role={{ ...role, payroll_scope: "selected", payroll_employee_ids: ["e1"] }} catalog={{}} statuses={[]} onClose={() => {}} onSaved={() => {}} />);
    await u.click(screen.getByRole("button", { name: "What they see" }));
    expect(await screen.findByRole("button", { name: "Ann" })).toHaveAttribute("aria-pressed", "true");
    await u.click(screen.getByRole("button", { name: "Only themselves" }));
    await u.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(bodies[0]).toMatchObject({ payroll_scope: "own", payroll_employee_ids: [] }));
  });
});
