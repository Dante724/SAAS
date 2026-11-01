// A1/A2 — saving a service sends every field back (nothing reset to defaults) and the
// "Show on online booking page" toggle drives Service.bookable.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import InventoryDrawer from "./InventoryDrawer";
import { serviceBody } from "./ServiceForm";

const SVC = {
  id: "s1", name: "Screen labour", description: "Teardown", category_id: null, category: null,
  is_labor: true, unit: "h", price: 120, cost_price: 40, duration_min: 90, flat_rate: true,
  warranty_days: 90, barcode: "SVC-1", bookable: true,
  allowed_employee_ids: ["e1"], required_resource_types: ["bay", "Lift"],
};

let calls: { method: string; url: string; body?: any }[] = [];
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method || "GET", url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.startsWith("/api/staff/employees")) return reply({ items: [{ id: "e1", name: "Ana" }, { id: "e2", name: "Ben" }] });
    if (url.startsWith("/api/resources")) return reply({ items: [{ id: "r1", kind: "lift" }] });
    if (url.startsWith("/api/catalog/categories")) return reply({ tree: [] });
    return reply({ ok: true });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const puts = () => calls.filter((c) => c.method === "PUT");

describe("serviceBody", () => {
  it("carries every stored service field", () => {
    const b = serviceBody(SVC);
    expect(b).toMatchObject({
      name: "Screen labour", description: "Teardown", is_labor: true, unit: "h", price: 120, cost_price: 40,
      duration_min: 90, flat_rate: true, warranty_days: 90, barcode: "SVC-1", bookable: true,
      allowed_employee_ids: ["e1"], required_resource_types: ["bay", "Lift"],
    });
  });
  it("normalises blanks instead of sending junk", () => {
    const b = serviceBody({ name: "  X ", barcode: "  ", warranty_days: "-3", unit: "" });
    expect(b.name).toBe("X");
    expect(b.barcode).toBeNull();
    expect(b.warranty_days).toBe(0);
    expect(b.unit).toBe("pc");
    expect(b.bookable).toBe(true);            // unset means shown, like the API default
    expect(b.allowed_employee_ids).toEqual([]);
  });
});

describe("Service drawer", () => {
  it("round-trips every field on save", async () => {
    const u = userEvent.setup();
    render(<InventoryDrawer kind="service" id="s1" initial={SVC} onClose={() => {}} />);
    await screen.findByRole("button", { name: "Ana" });
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].url).toBe("/api/catalog/services/s1");
    expect(puts()[0].body).toMatchObject({
      warranty_days: 90, barcode: "SVC-1", bookable: true, unit: "h",
      allowed_employee_ids: ["e1"], required_resource_types: ["bay", "Lift"],
    });
  });

  it("edits booking visibility, staff, resources, warranty and barcode", async () => {
    const u = userEvent.setup();
    render(<InventoryDrawer kind="service" id="s1" initial={SVC} onClose={() => {}} />);
    await u.click(screen.getByRole("switch", { name: "Show on online booking page" }));
    await u.click(await screen.findByRole("button", { name: "Ben" }));        // add
    await u.click(screen.getByRole("button", { name: "Ana" }));               // remove
    await u.click(await screen.findByRole("button", { name: "Lift" }));       // a resource kind, deselect
    await u.click(screen.getByRole("button", { name: "Room" }));              // standard kind, select
    const warranty = screen.getByLabelText("Warranty (days)");
    await u.clear(warranty); await u.type(warranty, "30");
    const barcode = screen.getByLabelText("Barcode");
    await u.clear(barcode); await u.type(barcode, "NEW-9");
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(puts()).toHaveLength(1));
    const b = puts()[0].body;
    expect(b.bookable).toBe(false);
    expect(b.allowed_employee_ids).toEqual(["e2"]);
    expect(b.required_resource_types).toEqual(["bay", "room"]);
    expect(b.warranty_days).toBe(30);
    expect(b.barcode).toBe("NEW-9");
  });

  it("adds a custom resource type", async () => {
    const u = userEvent.setup();
    render(<InventoryDrawer kind="service" id="s1" initial={{ ...SVC, required_resource_types: [] }} onClose={() => {}} />);
    await u.type(screen.getByLabelText("Add a resource type"), "Paint booth{Enter}");
    expect(screen.getByRole("button", { name: "Paint booth" })).toHaveAttribute("aria-pressed", "true");
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body.required_resource_types).toEqual(["Paint booth"]);
  });

  it("says so when the team can't load", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.startsWith("/api/staff/employees") ? reply({}, 500) : reply({ items: [], tree: [] }))));
    render(<InventoryDrawer kind="service" id="s1" initial={SVC} onClose={() => {}} />);
    expect(await screen.findByText(/Couldn't load the team/)).toBeInTheDocument();
  });
});
