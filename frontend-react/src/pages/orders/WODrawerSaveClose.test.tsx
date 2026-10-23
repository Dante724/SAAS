// The drawer's footer: "Save and close" saves what's unsaved and closes, and never changes the
// job's status (an accidental click must not finish a job). A plain close warns before
// throwing away unsaved notes.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import WODrawer from "./WODrawer";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let writes: { method: string; url: string; body: any }[] = [];

function open(status: string) {
  writes = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") writes.push({ method: init.method, url, body: init.body ? JSON.parse(String(init.body)) : null });
    if (url.includes("/statuses")) return reply({ items: [] });
    if (/\/orders\/o1$/.test(url)) return reply({ id: "o1", order_number: "WO-00001", status, lines: [] });
    return reply({ items: [] });
  }));
  const onClose = vi.fn();
  render(<MemoryRouter><WODrawer order={{ id: "o1", order_number: "WO-00001", status } as any} onClose={onClose} /></MemoryRouter>);
  return onClose;
}
const saveClose = () => screen.findByRole("button", { name: /Save and close/ });

afterEach(() => vi.unstubAllGlobals());

describe("WODrawer footer", () => {
  it.each(["in_repair", "dropped"])("Save and close on a %s job closes without changing the status", async (status) => {
    const onClose = open(status);
    fireEvent.click(await saveClose());
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(writes.filter((w) => /complete|status/.test(w.url))).toEqual([]);
  });

  it("Save and close saves unsaved bench notes first", async () => {
    const onClose = open("in_repair");
    fireEvent.change(await screen.findByPlaceholderText("Working notes…"), { target: { value: "Replaced flex cable" } });
    fireEvent.click(await saveClose());
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(writes).toContainEqual({ method: "PATCH", url: "/api/orders/o1/notes", body: { tech_notes: "Replaced flex cable", conclusion: "" } });
  });

  it("a plain Close asks before throwing away unsaved notes", async () => {
    const onClose = open("in_repair");
    fireEvent.change(await screen.findByPlaceholderText("Working notes…"), { target: { value: "half-typed" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Close" }).at(-1)!);
    expect(await screen.findByText(/aren't saved yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(onClose).not.toHaveBeenCalled();
    expect(writes).toEqual([]);
  });

  it("a plain Close with nothing unsaved just closes", async () => {
    const onClose = open("in_repair");
    await saveClose();
    fireEvent.click(screen.getAllByRole("button", { name: "Close" }).at(-1)!);
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
