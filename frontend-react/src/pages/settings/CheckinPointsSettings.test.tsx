// Settings › Public pages › Self check-in: points per location, each with its own QR.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
let canManage = true;
vi.mock("../../context/Me", () => ({ useMe: () => ({ can: () => canManage }) }));
vi.mock("../../lib/dialog", () => ({ confirmDialog: () => Promise.resolve(true) }));
const printed: string[] = [];
vi.mock("../../lib/safeHtml", () => ({ printHtmlInFrame: (h: string) => printed.push(h) }));
import CheckinPointsSettings from "./CheckinPointsSettings";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const pt = (over: Record<string, unknown>) => ({ id: "p1", location_id: "L1", location: "Main St", name: "Front door", mode: "auto", kiosk: false,
  job_type_ids: [], welcome_text: "", require_photo: false, show_wait: true, active: true, scans: 12, submits: 5, url: "https://x/c/aaa", path: "/c/aaa", ...over });
let calls: { m: string; url: string; body?: any }[] = [];

beforeEach(() => {
  calls = []; printed.length = 0; canManage = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const m = init?.method || "GET";
    calls.push({ m, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes("/forms/types")) return reply({ items: [{ id: "t1", name: "Brakes" }, { id: "t2", name: "Winterization" }] });
    if (url.includes("/staff/locations")) return reply({ items: [{ id: "L1", name: "Main St" }, { id: "L2", name: "Harbor yard" }] });
    if (url.endsWith("/poster")) return reply({ html: "<p>poster</p>" });
    if (url.endsWith("/rotate")) return reply(pt({ path: "/c/bbb", url: "https://x/c/bbb" }));
    if (m === "PUT") return reply(pt(JSON.parse(String(init!.body))));
    if (m === "POST") return reply(pt({ id: "p3" }), 201);
    return reply({ items: [pt({}), pt({ id: "p2", name: "Key box", mode: "key_drop" }), pt({ id: "p9", location_id: "L2", location: "Harbor yard", name: "Dock 3" })] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("CheckinPointsSettings", () => {
  it("groups points by location, each with its own code", async () => {
    render(<CheckinPointsSettings />);
    expect(await screen.findByText("Harbor yard")).toBeInTheDocument();
    expect(["Front door", "Key box", "Dock 3"].every((n) => screen.getByText(n))).toBe(true);
    expect(screen.getAllByText(/12 scans · 5 check-ins/)).toHaveLength(3);
  });

  it("saves the job types a point offers", async () => {
    render(<CheckinPointsSettings />);
    await screen.findByText("Harbor yard");
    await userEvent.click(screen.getByRole("button", { name: "Edit Dock 3" }));
    await userEvent.click(screen.getByLabelText("Winterization"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.m === "PUT" && c.url.endsWith("/checkins/points/p9"))).toBe(true));
    expect(calls.find((c) => c.m === "PUT")!.body.job_type_ids).toEqual(["t2"]);
  });

  it("prints the poster and replaces a code", async () => {
    render(<CheckinPointsSettings />);
    await screen.findByText("Harbor yard");
    await userEvent.click(screen.getAllByRole("button", { name: /Print poster/ })[0]);
    await waitFor(() => expect(printed).toEqual(["<p>poster</p>"]));
    await userEvent.click(screen.getByRole("button", { name: "More for Front door" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Replace QR code/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/checkins/points/p1/rotate"))).toBe(true));
  });

  it("is read-only without the settings permission", async () => {
    canManage = false;
    render(<CheckinPointsSettings />);
    await screen.findByText("Harbor yard");
    expect(screen.queryByRole("button", { name: "Add check-in point" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "More for Front door" }));
    expect(screen.queryByRole("menuitem", { name: /Replace QR code/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Edit Front door" }));
    expect(screen.getByLabelText("Point name")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
