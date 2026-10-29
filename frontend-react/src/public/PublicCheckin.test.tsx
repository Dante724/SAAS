// Self check-in: the form comes from the server, conditional fields follow the same rule as the
// server, and a check-in posts only what the form asks for.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../components/ScanButton", () => ({ default: () => <button type="button">Scan</button> }));
import PublicCheckin, { PublicCheckinStatus, visible, type Field } from "./PublicCheckin";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const page = (over: Record<string, unknown> = {}) => ({
  shop: { name: "Harbor Marine and Auto", logo: null, color: "#0f766e" },
  location: { name: "Main St" }, point: { name: "Front door", kiosk: false }, mode: "walk_in", waiting: 2, wait_min: 15,
  form: {
    asset_label: "Vehicle",
    groups: [{ key: "car", label: "Car", identifier: { primary: "vin", allowed: ["vin"] } }, { key: "truck", label: "Truck", identifier: { primary: "vin", allowed: ["vin"] } }],
    fields: [{ key: "make", label: "Make", type: "text" }, { key: "model", label: "Model", type: "text" }],
    reading: { key: "mileage", label: "Mileage" },
    job_types: [
      { id: "t1", name: "Brakes", fields: [{ key: "noise", label: "Noise when braking", type: "select", options: ["Squeal", "Grind"], required: true }] },
      { id: "t2", name: "Oil change", fields: [] },
    ],
    device_fields: [], terms: "Shop terms here.", require_photo: false, welcome_text: "",
  },
  ...over,
});

let posts: { url: string; body: any }[] = [];
beforeEach(() => {
  posts = [];
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, clearRect() {} })) as any;
  HTMLCanvasElement.prototype.toDataURL = vi.fn(() => "data:image/png;base64,AAA");
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

function serve(p = page()) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if ((init?.method || "GET") === "POST") {
      const body = JSON.parse(String(init!.body));
      posts.push({ url, body });
      if (url.endsWith("/identify")) return reply({ kind: "vin", make: "Honda", model: "Accord", year: 2019, problem: null });
      return reply({ status_token: "st1", queue_no: 3, kind: "walk_in", ahead: 2, wait_min: 15, texted: false }, 201);
    }
    return reply(p);
  }));
}

describe("visible", () => {
  const f = (op: string, value?: string): Field => ({ key: "x", label: "X", type: "text", show_when: { key: "trailer", op, value } });
  it("follows the shop's show-when rule", () => {
    expect(visible({ key: "x", label: "X", type: "text" }, {})).toBe(true);
    expect(visible(f("is", "Yes"), { trailer: "Yes" })).toBe(true);
    expect(visible(f("is", "Yes"), { trailer: "No" })).toBe(false);
    expect(visible(f("filled"), { trailer: "" })).toBe(false);
    expect(visible(f("gt", "100"), { trailer: "150" })).toBe(true);
  });
});

describe("PublicCheckin", () => {
  it("builds the form from the server and asks the chosen job type's questions", async () => {
    serve();
    render(<PublicCheckin token="tok" />);
    expect(await screen.findByText("Harbor Marine and Auto")).toBeInTheDocument();
    expect(screen.getByLabelText("VIN (optional)")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Mileage/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Add more details/ }));
    expect(screen.getByLabelText(/Mileage/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Noise when braking/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Brakes" }));
    expect(screen.getByLabelText(/Noise when braking/)).toBeInTheDocument();
  });

  it("decodes the VIN into one vehicle line instead of empty boxes, and sends what it found", async () => {
    serve();
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, reload: vi.fn() });
    render(<PublicCheckin token="tok" />);
    expect(await screen.findByLabelText("Make (optional)")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("VIN (optional)"), "1HGCM82633A004352");
    await userEvent.tab();
    expect(await screen.findByText("2019 Honda Accord")).toBeInTheDocument();
    expect(screen.getByText(/VIN ···4352/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Make/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Change/ }));
    expect(screen.getByLabelText("Make (optional)")).toHaveValue("Honda");
  });

  it("remembers a returning customer on their own phone", async () => {
    localStorage.setItem("tekly.checkin.me", JSON.stringify({ name: "Sam Patel", phone: "5550142290", email: "" }));
    serve();
    render(<PublicCheckin token="tok" />);
    expect(await screen.findByText("Welcome back, Sam")).toBeInTheDocument();
    expect(screen.getByLabelText("Your name")).toHaveValue("Sam Patel");
    await userEvent.click(screen.getByRole("button", { name: "Not you?" }));
    expect(screen.getByLabelText("Your name")).toHaveValue("");
    expect(localStorage.getItem("tekly.checkin.me")).toBeNull();
  });

  it("won't send until required answers and the signature are there, then posts the check-in", async () => {
    serve();
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, reload: vi.fn() });
    render(<PublicCheckin token="tok" />);
    await userEvent.click(await screen.findByRole("button", { name: "Brakes" }));
    await userEvent.type(screen.getByLabelText(/What's wrong/), "Squeals");
    await userEvent.type(screen.getByLabelText(/Your name/), "Sam Patel");
    await userEvent.type(screen.getByLabelText(/Mobile/), "5550142290");
    await userEvent.click(screen.getByRole("button", { name: "Check in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Noise when braking is required");
    await userEvent.selectOptions(screen.getByLabelText(/Noise when braking/), "Squeal");
    await userEvent.click(screen.getByRole("button", { name: "Check in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Sign to agree/);
    const pad = screen.getByLabelText("Sign here");
    pad.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, clientX: 5, clientY: 5 }));
    window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    await userEvent.click(screen.getByRole("button", { name: "Check in" }));
    await waitFor(() => expect(posts.some((p) => p.url === "/api/public/checkin/tok")).toBe(true));
    const sent = posts.find((p) => p.url === "/api/public/checkin/tok")!.body;
    expect(sent).toMatchObject({ job_type_id: "t1", name: "Sam Patel", issue: "Squeals", agree: true, answers: { noise: "Squeal" } });
    expect(sent.signature).toMatch(/^data:image\/png/);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/c/s/st1"));
  });

  it("switches to key drop when the shop is closed", async () => {
    serve(page({ mode: "key_drop" }));
    render(<PublicCheckin token="tok" />);
    expect(await screen.findByRole("heading", { name: "Dropping off keys" })).toBeInTheDocument();
    expect(screen.getByLabelText("Key tag number")).toBeInTheDocument();
    expect(screen.queryByText(/About 15 min wait/)).toBeNull();
    expect(screen.getByRole("button", { name: "Leave my keys" })).toBeInTheDocument();
  });

  it("says when a code was replaced", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ detail: "This code isn't used any more. Please ask at the counter." }, 410)));
    render(<PublicCheckin token="old" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("ask at the counter");
  });
});

describe("PublicCheckinStatus", () => {
  it("shows the customer's place and lets them leave the queue", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method || "GET"} ${url}`);
      return reply({ first_name: "Sam", queue_no: 3, state: "waiting", kind: "walk_in", ahead: 2, wait_min: 15,
        shop: { name: "Harbor" }, location: "Main St", summary: { asset: "2019 Honda Accord", issue: "Brakes" }, code: "st1", track_url: null });
    }));
    render(<PublicCheckinStatus token="st1" />);
    expect(await screen.findByText("#3")).toBeInTheDocument();
    expect(screen.getByText(/2 ahead of you · about 15 min/)).toBeInTheDocument();
    expect(screen.getByAltText("Your check-in code")).toHaveAttribute("src", "/api/public/checkin/s/st1/qr.svg");
    await userEvent.click(screen.getByRole("button", { name: /take me out of the queue/ }));
    expect(calls).toContain("POST /api/public/checkin/s/st1/cancel");
    expect(await screen.findByText("You've left the queue.")).toBeInTheDocument();
  });
});
