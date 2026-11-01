import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const get = vi.fn();
const post = vi.fn();
vi.mock("../api/client", () => ({ api: { get: (...a: any[]) => get(...a), post: (...a: any[]) => post(...a) }, readableError: (s: string) => s }));
const confirm = vi.fn();
vi.mock("../lib/dialog", () => ({ confirmDialog: (...a: any[]) => confirm(...a) }));

import DeviceList, { ago } from "./DeviceList";

const NOW = new Date().toISOString();
const ITEMS = [
  { id: "a", device: "Chrome on Mac", ip: "10.0.0.1", signed_in_at: NOW, last_active_at: NOW, this_device: true },
  { id: "b", device: "Safari on iPhone", ip: "10.0.0.2", signed_in_at: NOW, last_active_at: null, this_device: false },
  { id: "c", device: "Edge on Windows", ip: null, signed_in_at: NOW, last_active_at: null, this_device: false },
];

beforeEach(() => { get.mockReset(); post.mockReset(); confirm.mockReset(); });

describe("ago", () => {
  it("reads like a person would say it", () => {
    const now = Date.parse("2026-10-23T12:00:00Z");
    expect(ago("2026-10-23T11:59:30Z", now)).toBe("Active now");
    expect(ago("2026-10-23T11:45:00Z", now)).toBe("Active 15 min ago");
    expect(ago("2026-10-23T09:00:00Z", now)).toBe("Active 3 h ago");
    expect(ago("2026-10-22T12:00:00Z", now)).toBe("Active 1 day ago");
  });
});

describe("DeviceList (your own)", () => {
  it("marks this device and signs another out", async () => {
    get.mockResolvedValue({ items: ITEMS });
    post.mockResolvedValue({ ok: true });
    const u = userEvent.setup();
    render(<DeviceList base="/auth/sessions" />);
    expect(await screen.findByText("This device")).toBeTruthy();
    // no Sign out on this device's row; one on each of the others
    expect(screen.getAllByRole("button", { name: "Sign out" })).toHaveLength(2);
    await u.click(screen.getAllByRole("button", { name: "Sign out" })[0]);
    expect(post).toHaveBeenCalledWith("/auth/sessions/b/sign-out", {});
    expect(await screen.findByText("Signed out of Safari on iPhone.")).toBeTruthy();
    expect(screen.queryByText("Safari on iPhone")).toBeNull();
  });

  it("signs out all other devices after one confirm", async () => {
    get.mockResolvedValue({ items: ITEMS });
    post.mockResolvedValue({ ok: true });
    confirm.mockResolvedValue(true);
    const u = userEvent.setup();
    render(<DeviceList base="/auth/sessions" />);
    await u.click(await screen.findByRole("button", { name: "Sign out all other devices" }));
    expect(confirm.mock.calls[0][1].confirmLabel).toBe("Sign out 2 devices");
    expect(post).toHaveBeenCalledWith("/auth/sessions/sign-out-others", {});
  });

  it("says so when only this device is signed in", async () => {
    get.mockResolvedValue({ items: [ITEMS[0]] });
    render(<DeviceList base="/auth/sessions" />);
    expect(await screen.findByText("Only this device is signed in.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign out all other devices" })).toBeNull();
  });

  it("shows a retry when the list can't load", async () => {
    get.mockRejectedValueOnce(new Error("x")).mockResolvedValue({ items: [ITEMS[0]] });
    const u = userEvent.setup();
    render(<DeviceList base="/auth/sessions" />);
    await u.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Chrome on Mac")).toBeTruthy();
  });
});

describe("DeviceList (an admin looking at a teammate)", () => {
  it("offers to sign them out everywhere", async () => {
    get.mockResolvedValue({ items: ITEMS.map((d) => ({ ...d, this_device: false })) });
    post.mockResolvedValue({ ok: true });
    confirm.mockResolvedValue(true);
    const u = userEvent.setup();
    render(<DeviceList base="/staff/employees/e1/sessions" person="Maria" />);
    const row = (await screen.findByText("Edge on Windows")).closest("div")!.parentElement!;
    expect(within(row).getByRole("button", { name: "Sign out" })).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    expect(confirm.mock.calls[0][0]).toBe("Maria will need to sign in again on every device.");
    expect(post).toHaveBeenCalledWith("/staff/employees/e1/sessions/sign-out-all", {});
  });

  it("says when they aren't signed in anywhere", async () => {
    get.mockResolvedValue({ items: [] });
    render(<DeviceList base="/staff/employees/e1/sessions" person="Maria" />);
    expect(await screen.findByText("Maria isn't signed in anywhere.")).toBeTruthy();
  });
});


describe("DeviceList when the account isn't yours to manage", () => {
  it("shows a short note instead of an error", async () => {
    get.mockRejectedValue(Object.assign(new Error("no"), { status: 403 }));
    render(<DeviceList base="/staff/employees/owner/sessions" person="Rahul" />);
    expect(await screen.findByText("Only the shop owner can manage these devices.")).toBeTruthy();
    expect(screen.queryByText("Couldn't load devices.")).toBeNull();
  });
});
