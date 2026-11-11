// Settings › Team › Commissions: how a task shared by several people pays commission.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
let payrollOk = true;
vi.mock("../../context/Me", () => ({ useMe: () => ({ me: { id: "me" }, can: (c?: string) => c !== "can_manage_payroll" || payrollOk }) }));

import { TaskCommissionCard } from "./TeamSettings";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let calls: { method: string; url: string; body: any }[] = [];
let getStatus = 200;
let putStatus = 200;
let stored: any = {};

beforeEach(() => {
  calls = []; getStatus = 200; putStatus = 200; stored = {}; payrollOk = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url === "/api/payroll/settings" && method === "GET") return reply(stored, getStatus);
    if (url === "/api/payroll/settings" && method === "PUT") return reply({ detail: "no" }, putStatus);
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("When a task has several people", () => {
  it("defaults to Split equally and saves a change", async () => {
    const flash = vi.fn();
    const u = userEvent.setup();
    render(<TaskCommissionCard onFlash={flash} />);
    const sel = screen.getByRole("combobox", { name: "When a task has several people" });
    await waitFor(() => expect(sel).not.toBeDisabled());
    expect(sel).toHaveValue("split");
    await u.selectOptions(sel, "each_full");
    expect(calls.find((c) => c.method === "PUT")).toMatchObject({ url: "/api/payroll/settings", body: { task_commission_mode: "each_full" } });
    await waitFor(() => expect(flash).toHaveBeenCalledWith("Saved. Tasks closed from now on are paid this way."));
  });

  it("shows the stored choice and puts it back when saving fails", async () => {
    stored = { task_commission_mode: "first_only" }; putStatus = 500;
    const flash = vi.fn();
    const u = userEvent.setup();
    render(<TaskCommissionCard onFlash={flash} />);
    const sel = screen.getByRole("combobox", { name: "When a task has several people" });
    await waitFor(() => expect(sel).toHaveValue("first_only"));
    await u.selectOptions(sel, "split");
    await waitFor(() => expect(sel).toHaveValue("first_only"));
    expect(flash.mock.calls[0][0]).toMatch(/Couldn't save/);
  });

  it("is greyed with a reason when the server doesn't have it yet", async () => {
    getStatus = 404;
    render(<TaskCommissionCard onFlash={vi.fn()} />);
    const sel = screen.getByRole("combobox", { name: "When a task has several people" });
    await waitFor(() => expect(sel.closest("[title]")).toHaveAttribute("title", "This setting arrives with the next server update."));
    expect(sel).toBeDisabled();
  });

  it("is greyed with a reason for people who don't manage payroll, and says it applies from now on", async () => {
    payrollOk = false;
    render(<TaskCommissionCard onFlash={vi.fn()} />);
    const sel = screen.getByRole("combobox", { name: "When a task has several people" });
    await waitFor(() => expect(sel.closest("[title]")).toHaveAttribute("title", "Only people who manage payroll can change this"));
    expect(sel).toBeDisabled();
    expect(screen.getByText(/Changes apply to tasks closed from now on/)).toBeInTheDocument();
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Split the amount between them", "Pay each person the full amount", "Pay only the main person"]);
  });

  it("offers the modes the server lists (task_commission_modes)", async () => {
    stored = { task_commission_mode: "split", task_commission_modes: ["split", "first_only"] };
    render(<TaskCommissionCard onFlash={vi.fn()} />);
    const sel = screen.getByRole("combobox", { name: "When a task has several people" });
    await waitFor(() => expect(sel).not.toBeDisabled());
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Split the amount between them", "Pay only the main person"]);
  });
});
