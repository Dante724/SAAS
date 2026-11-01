// A technician who tries to close a job that still owes money sees the server's plain sentence,
// not "Failed: …" or a status code, and the status snaps back.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import WODrawer from "./WODrawer";

const MSG = "WO-00012 still has $40.00 to pay. Take the payment first, or ask someone who takes payments to close it.";
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const statuses = [
  { id: "s1", key: "ready", name: "Ready", status_group: "ready", color: "#2cd4c0", sort_order: 1 },
  { id: "s2", key: "done", name: "Done", status_group: "closed", color: "#3dca7a", sort_order: 2, is_terminal: true },
];
const order: any = { id: "o1", order_number: "WO-00012", status: "ready", client: { name: "Dana" }, lines: [], total: 40 };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PATCH" && url.endsWith("/orders/o1/status")) return reply({ detail: MSG }, 403);
    if (url.includes("/statuses")) return reply({ items: statuses });
    if (url.endsWith("/orders/o1")) return reply(order);
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("WODrawer status change blocked by the server", () => {
  it("shows the server's sentence and keeps the old status", async () => {
    const u = userEvent.setup();
    render(<MemoryRouter><WODrawer order={order} onClose={() => {}} /></MemoryRouter>);
    await u.click(await screen.findByRole("button", { name: /^Ready/ }));
    await u.click(await screen.findByRole("button", { name: /^Done/ }));
    expect(await screen.findByText(MSG)).toBeInTheDocument();
    expect(screen.queryByText(/Failed:|403/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Ready/ })).toBeInTheDocument();
  });
});
