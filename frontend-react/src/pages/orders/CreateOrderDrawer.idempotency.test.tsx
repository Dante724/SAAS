// Council M13: creating a job sends an Idempotency-Key. A retry of the same job (the first try
// lost its connection, maybe after the server made it) sends the same key, so the server hands
// back that job instead of making a second one; the next job gets a fresh key. A lost
// connection reads as a plain sentence, not the browser's "Failed to fetch".
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../context/Me", () => ({ useMe: () => ({ can: () => true, me: null }), hasFeature: () => false }));
vi.mock("../../components/sense/Sense", () => ({ JobSuggestions: () => null }));
vi.mock("../../lib/autoPrint", () => ({ autoPrintForJob: async () => {} }));
vi.mock("../../components/AutoPrintPicker", () => ({ default: () => null }));

import CreateOrderDrawer from "./CreateOrderDrawer";
import { NO_CONNECTION } from "../../api/client";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const keyOf = (init?: RequestInit) => (init?.headers as Record<string, string> | undefined)?.["Idempotency-Key"];
let creates: (string | undefined)[] = [];
let failNext = 0;

beforeEach(() => {
  creates = []; failNext = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST" && url === "/api/orders") {
      creates.push(keyOf(init));
      if (failNext > 0) { failNext--; throw new TypeError("Failed to fetch"); }
      return reply({ id: `o${creates.length}`, order_number: "WO-1" });
    }
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("CreateOrderDrawer job create", () => {
  it("a retry after a lost connection re-sends the same key; the next job gets a new one", async () => {
    const u = userEvent.setup();
    const onCreated = vi.fn();
    render(<MemoryRouter><CreateOrderDrawer prefillClient={{ id: "c1", name: "Ana" }} onClose={() => {}} onCreated={onCreated} /></MemoryRouter>);
    failNext = 1;
    await u.click(await screen.findByRole("button", { name: /Next: services and products/ }));
    await u.click(await screen.findByRole("button", { name: "Save and create another" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(NO_CONNECTION);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/Failed to fetch/);
    expect(onCreated).not.toHaveBeenCalled();

    await u.click(screen.getByRole("button", { name: "Save and create another" }));   // the retry
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(creates).toHaveLength(2);
    expect(creates[0]).toBeTruthy();
    expect(creates[1]).toBe(creates[0]);

    // "Create another" starts a new job: a new key.
    await u.click(await screen.findByRole("button", { name: /Next: services and products/ }));
    await u.click(await screen.findByRole("button", { name: "Save and create another" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(2));
    expect(creates).toHaveLength(3);
    expect(creates[2]).toBeTruthy();
    expect(creates[2]).not.toBe(creates[0]);
  });
});
