// Inquiries › Walk-ins: the self check-in queue. Call next only calls; Convert opens the job.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const navigate = vi.fn();
vi.mock("react-router-dom", () => ({ useNavigate: () => navigate }));
vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
let allow = true;
vi.mock("../../context/Me", () => ({ useMe: () => ({ can: (c: string) => c !== "can_create_tickets" || allow }) }));
const printed: string[] = [];
vi.mock("../../lib/autoPrint", () => ({ autoPrintForJob: (id: string) => { printed.push(id); return Promise.resolve(1); } }));
import WalkinsView from "./WalkinsView";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const w = (over: Record<string, unknown>) => ({ id: "c1", lead_id: "l1", queue_no: 1, kind: "walk_in", state: "waiting", name: "Sam Patel",
  asset: "2019 Honda Accord", reading: 62400, reading_kind: "mileage", photos: 0, point: "Front door", waited_min: 4, issue: "Brakes", ...over });
let calls: string[] = [];

beforeEach(() => {
  calls = []; printed.length = 0; navigate.mockReset(); allow = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method || "GET"} ${url}`);
    if (url.endsWith("/call")) return reply({ ok: true, state: "called", texted: true });
    if (url.endsWith("/convert")) return reply({ ok: true, order_id: "o9", order_number: "WO-9" });
    if (url.includes("/by-code/")) return reply(w({ id: "c2", name: "Lena", queue_no: 2 }));
    return reply({
      waiting: [w({}), w({ id: "c2", queue_no: 2, name: "Lena Ortiz", seen_before: { device_id: "d1" } })],
      called: [w({ id: "c3", queue_no: 0, state: "called", name: "Mark Chu" })],
      key_drops: [w({ id: "c4", kind: "key_drop", name: "Dana Wells", key_tag: "17", photos: 1 })], done: [],
    });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("WalkinsView", () => {
  it("lists the queue in order, the people being served and overnight key drops", async () => {
    render(<WalkinsView />);
    expect(await screen.findByText("Sam Patel")).toBeInTheDocument();
    expect(screen.getByText("Seen before")).toBeInTheDocument();
    expect(screen.getAllByText(/62,400 mi/).length).toBeGreaterThan(0);
    expect(screen.getByText("Being served")).toBeInTheDocument();
    expect(screen.getByText(/key tag 17/)).toBeInTheDocument();
  });

  it("Call next calls the customer and never creates a job", async () => {
    render(<WalkinsView />);
    await screen.findByText("Sam Patel");
    await userEvent.click(screen.getAllByRole("button", { name: "Call next" })[0]);
    await waitFor(() => expect(calls).toContain("POST /api/checkins/c1/call"));
    expect(calls.some((c) => c.includes("/convert"))).toBe(false);
    expect(await screen.findByText(/was texted to come to the counter/)).toBeInTheDocument();
  });

  it("Convert opens the filled work order and prints what's set to print", async () => {
    render(<WalkinsView />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Create work order" }))[0]);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/orders?open=o9"));
    expect(calls).toContain("POST /api/checkins/c3/convert");
    expect(printed).toEqual(["o9"]);
  });

  it("hides Convert from roles that can't create jobs", async () => {
    allow = false;
    render(<WalkinsView />);
    await screen.findByText("Mark Chu");
    expect(screen.queryByRole("button", { name: "Create work order" })).toBeNull();
  });

  it("opens a check-in from the customer's scanned code", async () => {
    render(<WalkinsView />);
    await screen.findByText("Sam Patel");
    await userEvent.type(screen.getByLabelText("Customer's check-in code"), "st-abc{enter}");
    await waitFor(() => expect(calls).toContain("GET /api/checkins/by-code/st-abc"));
    expect(calls).toContain("POST /api/checkins/c2/call");
  });
});
