// The customer drawer: new work order and new estimate both start from here, money actions are
// gated by the same permissions the server checks, and work order rows open the work order.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("./CreateOrderDrawer", () => ({ default: ({ isEstimate }: { isEstimate?: boolean }) => <div data-testid="create">{isEstimate ? "estimate" : "work order"}</div> }));
vi.mock("../memberships/shared", () => ({ MembershipsCard: () => null }));
vi.mock("../memberships/MembershipsView", () => ({ MembershipDrawer: () => null }));
let denied: string[] = [];
vi.mock("../../context/Me", () => ({ useMe: () => ({ can: (c?: string) => !c || !denied.includes(c), me: null }) }));

import ClientDrawer from "./ClientDrawer";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div>; }

function open() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (/\/clients\/c1$/.test(url)) return reply({ id: "c1", name: "Ana Diaz", balance: 0, order_count: 1 });
    if (url.includes("/orders?client_id=c1")) return reply({ items: [{ id: "o9", order_number: "WO-00009", status: "in_repair", total: 50 }] });
    return reply({ items: [] });
  }));
  const onClose = vi.fn();
  render(<MemoryRouter initialEntries={["/clients"]}><Routes><Route path="*" element={<><ClientDrawer clientId="c1" onClose={onClose} /><Where /></>} /></Routes></MemoryRouter>);
  return onClose;
}

afterEach(() => { vi.unstubAllGlobals(); denied = []; });

describe("ClientDrawer", () => {
  it("offers a new estimate next to a new work order", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: /New estimate/ }));
    expect(screen.getByTestId("create")).toHaveTextContent("estimate");
  });

  it("hides actions the role can't use", async () => {
    denied = ["can_create_tickets", "can_create_estimates", "can_make_client_payments"];
    open();
    await screen.findByText("Ana Diaz");
    expect(screen.queryByRole("button", { name: /New work order/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /New estimate/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add credit" })).toBeNull();
  });

  it("a work order row opens that work order", async () => {
    const onClose = open();
    fireEvent.click(await screen.findByRole("button", { name: /Work orders/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Open work order WO-00009" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/orders?open=o9");
    expect(onClose).toHaveBeenCalled();
  });
});
