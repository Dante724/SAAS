// The printer button on the new-job, close-job and POS screens: tick what prints by itself.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

let canManage = true;
vi.mock("../context/Me", async (orig) => {
  const real: any = await orig();
  return { ...real, useMe: () => ({ can: (c: string) => c !== "can_manage_settings" || canManage }) };
});
vi.mock("./Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
import AutoPrintPicker from "./AutoPrintPicker";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let tpls: any[] = [];
let puts: { url: string; body: any }[] = [];

beforeEach(() => {
  canManage = true; puts = [];
  tpls = [
    { id: "k", name: "Key tag", auto_print_events: ["create"], auto_print_allowed: ["create", "close"] },
    { id: "r", name: "Sales receipt", auto_print_events: [], auto_print_allowed: ["create", "close", "sale"] },
    { id: "p", name: "Price tag", auto_print_events: [], auto_print_allowed: [] },
  ];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if ((init?.method || "GET") === "PUT") {
      const body = JSON.parse(String(init!.body));
      puts.push({ url, body });
      return reply({ auto_print_events: body.events });
    }
    return reply({ items: tpls });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("AutoPrintPicker", () => {
  it("shows what's on and lists only documents that can print at this moment", async () => {
    render(<AutoPrintPicker event="create" />);
    await screen.findByText("Key tag");
    await userEvent.click(screen.getByRole("button", { name: /Print automatically when a job is created/ }));
    expect(screen.getByRole("checkbox", { name: "Key tag" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Sales receipt" })).not.toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "Price tag" })).toBeNull();
  });

  it("switches a document on and off for this moment only, keeping its other moments", async () => {
    tpls[0].auto_print_events = ["create", "close"];
    render(<AutoPrintPicker event="close" />);
    await userEvent.click(await screen.findByRole("button", { name: /a job is closed/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Key tag" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].url).toContain("/documents/templates/k/auto-print");
    expect(puts[0].body).toEqual({ events: ["create"] });
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Key tag" })).not.toBeChecked());
    await userEvent.click(screen.getByRole("checkbox", { name: "Sales receipt" }));
    await waitFor(() => expect(puts[1]?.body).toEqual({ events: ["close"] }));
  });

  it("says auto-print is off when nothing is ticked", async () => {
    tpls[0].auto_print_events = [];
    render(<AutoPrintPicker event="sale" />);
    expect(await screen.findByText("Auto-print off")).toBeInTheDocument();
  });

  it("is read-only for someone who can't manage settings", async () => {
    canManage = false;
    render(<AutoPrintPicker event="create" />);
    await userEvent.click(await screen.findByRole("button", { name: /a job is created/ }));
    expect(screen.getByRole("checkbox", { name: "Key tag" })).toBeDisabled();
    expect(screen.getByText(/Only someone who manages settings/)).toBeInTheDocument();
  });
});
