// Appointments › Scheduler: day navigation, opening hours, empty day, new-appointment form.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
import AppointmentsHub from "./AppointmentsHub";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const pad = (n: number) => String(n).padStart(2, "0");
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const today = new Date();
const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
const T = ymd(today), N = ymd(tomorrow);
const label = (d: Date) => `${d.toLocaleDateString([], { weekday: "short" })}, ${d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`;
const week = (day: object) => Object.fromEntries(["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map((k) => [k, day]));

let hours: object = week({ open: "10:00", close: "16:00" });
let calls: { m: string; url: string; body?: any }[] = [];
const appt = (day: string, hm: string) => ({ id: `a-${day}`, client_name: "Dana Fix", service_name: "Screen repair", scheduled_for: `${day}T${hm}:00`, duration_min: 60, status: "booked" });

beforeEach(() => {
  calls = []; hours = week({ open: "10:00", close: "16:00" });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const m = init?.method || "GET";
    calls.push({ m, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes("/staff/locations")) return reply({ items: [{ id: "L1", name: "Main St", working_hours: hours }] });
    if (url.includes("/staff/employees")) return reply({ items: [] });
    if (url.includes("/catalog/services")) return reply({ items: [] });
    if (url.includes("/clients")) return reply({ items: [{ id: "c1", name: "Dana Fix", phone: "555-0100" }] });
    if (url.includes("/appointments/availability")) return reply({ slots: [{ start: "10:00", end: "11:00", start_iso: `${T}T10:00:00` }] });
    if (url.includes(`/appointments?date_str=${N}`)) return reply({ items: [appt(N, "18:30")] });
    if (url.includes("/appointments?date_str=")) return reply({ items: [] });
    if (m === "POST") return reply({ id: "new" }, 201);
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("AppointmentsHub scheduler", () => {
  it("moves to the next day and loads that day's appointments", async () => {
    render(<AppointmentsHub />);
    await userEvent.click(screen.getByRole("button", { name: /Scheduler/ }));
    expect(await screen.findByTestId("scheduler-day")).toHaveTextContent(label(today));
    await waitFor(() => expect(calls.some((c) => c.url.includes(`/appointments?date_str=${T}`))).toBe(true));
    // Today has nothing booked: calm empty state with the New appointment action.
    expect(await screen.findByText("No appointments on this day")).toBeInTheDocument();
    expect(screen.getByText(/Open 10:00/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(screen.getByTestId("scheduler-day")).toHaveTextContent(label(tomorrow));
    await waitFor(() => expect(calls.some((c) => c.url.includes(`/appointments?date_str=${N}`))).toBe(true));
    // 18:30 is after closing (16:00): the grid widens so the appointment still shows.
    expect(await screen.findByText(/Screen repair/)).toBeInTheDocument();
    expect(screen.getByText("18:00")).toBeInTheDocument();
    expect(screen.queryByText("09:00")).not.toBeInTheDocument();

    // Switching views keeps the chosen day.
    await userEvent.click(screen.getByRole("button", { name: /Agenda/ }));
    await userEvent.click(screen.getByRole("button", { name: /Scheduler/ }));
    expect(await screen.findByTestId("scheduler-day")).toHaveTextContent(label(tomorrow));
  });

  it("shows Closed for a closed day and falls back to the server's 09:00–17:00 window", async () => {
    hours = week({ closed: true });
    render(<AppointmentsHub />);
    await userEvent.click(screen.getByRole("button", { name: /Scheduler/ }));
    await userEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(await screen.findByText(/Screen repair/)).toBeInTheDocument();
    expect(screen.getByText("Closed")).toBeInTheDocument();
    expect(screen.getByText("09:00")).toBeInTheDocument();
    expect(screen.queryByText("08:00")).toBeNull();
    expect(screen.getByText("16:00")).toBeInTheDocument();
  });

  it("books from the empty day with the date filled in, a picked client and a slot", async () => {
    render(<AppointmentsHub />);
    await userEvent.click(screen.getByRole("button", { name: /Scheduler/ }));
    await screen.findByText("No appointments on this day");
    const btns = screen.getAllByRole("button", { name: /New appointment/ });
    await userEvent.click(btns[btns.length - 1]);
    // The date is pre-filled, so open slots load straight away; the exact time hides behind "Custom time".
    await userEvent.click(await screen.findByRole("button", { name: "10:00–11:00" }));
    expect(screen.getByRole("button", { name: "Custom time" })).toBeInTheDocument();
    await userEvent.type(screen.getByRole("combobox", { name: "Client" }), "Dan");
    await userEvent.click(await screen.findByRole("option", { name: /Dana Fix/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create appointment" }));
    await waitFor(() => expect(calls.some((c) => c.m === "POST")).toBe(true));
    const body = calls.find((c) => c.m === "POST")!.body;
    expect(body).toMatchObject({ client_id: "c1", client_name: "Dana Fix", scheduled_for: `${T}T10:00:00` });
  });
});
