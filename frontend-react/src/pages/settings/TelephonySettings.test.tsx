// D1/D2 settings card: Test-mode badge, connecting Twilio (keys never echoed back), the
// "Test connection" button, saving the contacts-from-calls automation, simulating a call.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TelephonySettings, { statusBadge, type TelConfig } from "./TelephonySettings";

const tw = (over: Partial<TelConfig["twilio"]> = {}): TelConfig["twilio"] => ({
  connected: false, mode: null, test_mode: false, live_allowed: false,
  voice_url: "https://shop.example/api/comms/twilio/voice?org_id=o1",
  status_url: "https://shop.example/api/comms/twilio/status?org_id=o1", test_number: "+15005550006", ...over });
const cfg = (over: Partial<TelConfig> = {}): TelConfig => ({ provider: "simulated", enabled: false, caller_id_number: null, forward_to: null, twilio: tw(), ...over });
const auto = { enabled: false, config: { direction: "incoming", tag_ids: [], assign_manager: true } };
const tags = { items: [{ id: "t1", name: "Caller", color: "#2563eb" }, { id: "t2", name: "VIP", color: "#16a34a" }] };

type Route = (method: string, url: string, body: any) => unknown;
let calls: { method: string; url: string; body: any }[] = [];
function serve(route: Route) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url: input, body });
    const res = route(method, input, body);
    const status = res instanceof Error ? 422 : 200;
    const json = res instanceof Error ? { detail: res.message } : res;
    return { ok: status < 400, status, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => json, text: async () => JSON.stringify(json) } as Response;
  }));
}
function base(extra: Route = () => undefined): Route {
  return (m, u, b) => extra(m, u, b) ?? (u.endsWith("/telephony/config") ? cfg() : u.endsWith("/automation/contacts") ? auto : u.endsWith("/tags") ? tags : {});
}
beforeEach(() => serve(base()));
afterEach(() => vi.unstubAllGlobals());

describe("statusBadge", () => {
  it("names the state, with Test mode for test credentials", () => {
    expect(statusBadge(cfg())).toEqual({ text: "Off", tone: "off" });
    expect(statusBadge(cfg({ enabled: true }))).toEqual({ text: "Simulated", tone: "test" });
    expect(statusBadge(cfg({ enabled: true, provider: "twilio", twilio: tw({ connected: true, mode: "test", test_mode: true }) })).text).toBe("Twilio · Test mode");
    expect(statusBadge(cfg({ enabled: true, provider: "twilio", twilio: tw({ connected: true, mode: "live" }) })).text).toBe("Twilio · Live");
  });
});

describe("TelephonySettings", () => {
  it("shows a loading state, then an error with retry when loading fails", async () => {
    serve(() => new Error("Network down"));
    render(<TelephonySettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Network down");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("connects Twilio test credentials, clears the fields and shows Test mode", async () => {
    const u = userEvent.setup();
    serve(base((m, url) => {
      if (m === "PUT" && url.endsWith("/telephony/twilio")) return tw({ connected: true, mode: "test", test_mode: true });
      return undefined;
    }));
    render(<TelephonySettings />);
    await u.selectOptions(await screen.findByLabelText("Provider"), "twilio");
    const connect = screen.getByRole("button", { name: "Connect Twilio" });
    expect(connect).toBeDisabled();
    await u.type(screen.getByLabelText("Account SID"), "AC123");
    await u.type(screen.getByLabelText("Auth token"), "secret-token");
    await u.click(connect);
    expect(await screen.findByRole("status")).toHaveTextContent(/Connected with test credentials/);
    expect(screen.getByText("Test mode")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT" && c.url.endsWith("/telephony/twilio"))?.body).toEqual({ account_sid: "AC123", auth_token: "secret-token" });
    expect(screen.queryByDisplayValue("secret-token")).toBeNull();
    // the webhook URLs to paste into Twilio
    expect(screen.getByText(/twilio\/voice\?org_id=o1/)).toBeInTheDocument();
  });

  it("runs Test connection and reports Twilio's error", async () => {
    const u = userEvent.setup();
    let fail = false;
    serve(base((m, url) => {
      if (url.endsWith("/telephony/config")) return cfg({ provider: "twilio", enabled: true, caller_id_number: "+15005550006", twilio: tw({ connected: true, mode: "test", test_mode: true }) });
      if (m === "POST" && url.endsWith("/twilio/test")) return fail ? new Error("Twilio rejected the Account SID or auth token.") : { ok: true, mode: "test", account_name: null };
      return undefined;
    }));
    render(<TelephonySettings />);
    expect(await screen.findByTestId("tel-badge")).toHaveTextContent("Twilio · Test mode");
    await u.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connection works\. These are test credentials/)).toBeInTheDocument();
    fail = true;
    await u.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Twilio rejected");
    // no simulated call on a real provider
    expect(screen.queryByRole("button", { name: /Simulate call/ })).toBeNull();
  });

  it("saves the contacts-from-calls automation with the picked tags", async () => {
    const u = userEvent.setup();
    serve(base((m, url, b) => (m === "PUT" && url.endsWith("/automation/contacts") ? { enabled: b.enabled, config: { direction: b.direction, tag_ids: b.tag_ids, assign_manager: b.assign_manager } } : undefined)));
    render(<TelephonySettings />);
    await u.click(await screen.findByLabelText("Create contacts from calls"));
    await u.selectOptions(screen.getByLabelText("Which calls"), "all");
    await u.click(screen.getByRole("button", { name: /VIP/ }));
    expect(screen.getByRole("button", { name: /VIP/ })).toHaveAttribute("aria-pressed", "true");
    await u.click(screen.getByRole("button", { name: "Save automation" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT" && c.url.endsWith("/automation/contacts"))?.body)
      .toEqual({ enabled: true, direction: "all", tag_ids: ["t2"], assign_manager: true }));
  });

  it("saves the countries staff can call, defaulting to the shop's own code", async () => {
    const u = userEvent.setup();
    serve(base((m, url, b) => {
      if (url.endsWith("/telephony/config") && m === "GET") return cfg({ destinations: { default: ["1"], override: null, effective: ["1"] } });
      if (m === "PUT" && url.endsWith("/telephony/config")) return cfg({ destinations: { default: ["1"], override: b.allowed_prefixes.map((p: string) => p.replace("+", "")), effective: ["1", "44"] } });
      return undefined;
    }));
    render(<TelephonySettings />);
    const input = await screen.findByLabelText("Countries staff can call");
    expect(input).toHaveAttribute("placeholder", "Your country only (+1)");
    await u.type(input, "+1, +44");
    await u.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT" && c.url.endsWith("/telephony/config"))?.body.allowed_prefixes).toEqual(["+1", "+44"]));
    expect(await screen.findByDisplayValue("+1, +44")).toBeInTheDocument();
  });

  it("simulates an inbound call and says a contact was created", async () => {
    const u = userEvent.setup();
    serve(base((m, url) => (m === "POST" && url.endsWith("/simulate/inbound") ? { id: "c1", client_id: "x", client_created: true, matched: false } : undefined)));
    render(<TelephonySettings />);
    const btn = await screen.findByRole("button", { name: /Simulate call/ });
    expect(btn).toBeDisabled();
    await u.type(screen.getByLabelText("Caller's number"), "+14155550100");
    await u.click(btn);
    expect(await screen.findByText(/new contact was created/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/simulate/inbound"))?.body).toEqual({ from_number: "+14155550100", extension: null, missed: false });
  });

  it("a simulated missed call says it went to Inquiries", async () => {
    const u = userEvent.setup();
    serve(base((m, url) => (m === "POST" && url.endsWith("/simulate/inbound") ? { id: "c1", client_id: null, client_created: false, matched: false, lead_id: "l1" } : undefined)));
    render(<TelephonySettings />);
    await u.type(await screen.findByLabelText("Caller's number"), "+14155550100");
    await u.click(screen.getByLabelText("Nobody answers (a missed call)"));
    await u.click(screen.getByRole("button", { name: /Simulate call/ }));
    expect(await screen.findByText(/in Inquiries with a call-back task/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/simulate/inbound"))?.body.missed).toBe(true);
  });

  it("saves the missed-call settings", async () => {
    const u = userEvent.setup();
    serve(base((m, url, b) => {
      if (url.endsWith("/leads/missed-call-settings")) return m === "PUT" ? { enabled: b.enabled, config: b } : { enabled: true, config: { merge_hours: 24, sla_minutes: 30, notify: true, fallback_employee_id: null } };
      if (url.endsWith("/staff/employees")) return [{ id: "e1", name: "Sam" }];
      return undefined;
    }));
    render(<TelephonySettings />);
    const merge = await screen.findByLabelText("Add repeat calls within (hours)");
    await u.clear(merge); await u.type(merge, "48");
    await u.selectOptions(screen.getByLabelText("When nobody is clocked in, alert"), "e1");
    await u.click(screen.getByRole("button", { name: "Save missed-call settings" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/leads/missed-call-settings"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT" && c.url.endsWith("/leads/missed-call-settings"))!.body)
      .toEqual({ enabled: true, merge_hours: 48, sla_minutes: 30, notify: true, fallback_employee_id: "e1" });
  });

  it("offers Aircall only when the server has it switched on", async () => {
    const ac = { available: true, connected: false, webhook_url: "https://shop.example/api/telephony/aircall/webhook?org_id=o1" };
    serve(base((m, url) => (url.endsWith("/telephony/config") ? cfg({ aircall: ac }) : undefined)));
    const u = userEvent.setup();
    render(<TelephonySettings />);
    await u.selectOptions(await screen.findByLabelText("Provider"), "aircall");
    expect(screen.getByText(ac.webhook_url)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save token" })).toBeDisabled();
  });

  it("hides Aircall when the server switch is off", async () => {
    serve(base((m, url) => (url.endsWith("/telephony/config") ? cfg({ aircall: { available: false, connected: false, webhook_url: "" } }) : undefined)));
    render(<TelephonySettings />);
    await screen.findByLabelText("Provider");
    expect(screen.queryByRole("option", { name: "Aircall" })).toBeNull();
  });
});
