// The footer's finish button: "Mark done and close" on an open job, gone once the job is
// already finished (Done / Delivered / Not collected), where it would drag it back to Done.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import WODrawer from "./WODrawer";

const STATUSES = [
  { id: "1", key: "in_repair", name: "In repair", status_group: "in_progress", sort_order: 3, is_terminal: false },
  { id: "2", key: "done", name: "Done", status_group: "closed", sort_order: 5, is_terminal: true },
  { id: "3", key: "delivered", name: "Delivered", status_group: "closed", sort_order: 6, is_terminal: true },
  { id: "4", key: "dropped", name: "Not collected", status_group: "dropped", sort_order: 7, is_terminal: true },
];
const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

function open(status: string, extra: Record<string, unknown> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/statuses")) return reply({ items: STATUSES });
    if (/\/orders\/o1$/.test(url)) return reply({ id: "o1", order_number: "WO-00001", status, lines: [] });
    return reply({ items: [] });
  }));
  render(<MemoryRouter><WODrawer order={{ id: "o1", order_number: "WO-00001", status, ...extra } as any} onClose={() => {}} /></MemoryRouter>);
}

afterEach(() => vi.unstubAllGlobals());

describe("WODrawer finish button", () => {
  it("offers Mark done and close on a job that's still open", async () => {
    open("in_repair");
    expect(await screen.findByRole("button", { name: /Mark done and close/ })).toBeInTheDocument();
  });

  it.each(["done", "delivered", "dropped"])("hides it once the job is %s", async (status) => {
    open(status);
    await screen.findAllByRole("button", { name: "Close" });
    await new Promise((r) => setTimeout(r, 50));   // let the statuses load
    expect(screen.queryByRole("button", { name: /Mark .* and close/ })).toBeNull();
  });

  it("hides it on an estimate", async () => {
    open("in_repair", { is_estimate: true });
    await screen.findAllByRole("button", { name: "Close" });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("button", { name: /Mark .* and close/ })).toBeNull();
  });
});
