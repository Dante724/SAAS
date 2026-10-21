// D1: the contact drawer's Call button.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CallButton, { _resetCallingCache } from "./CallButton";

const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let posted: any[] = [];
function serve(enabled: boolean) {
  posted = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if ((init?.method || "GET") === "POST") { posted.push(JSON.parse(String(init!.body))); return reply({ provider: "twilio", test_mode: true }); }
    return reply({ enabled });
  }));
}
beforeEach(() => _resetCallingCache());
afterEach(() => vi.unstubAllGlobals());

describe("CallButton", () => {
  it("is hidden when calling is off", async () => {
    serve(false);
    const { container } = render(<CallButton phone="+14155550100" clientId="c1" />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("places a call for the contact", async () => {
    serve(true);
    const u = userEvent.setup();
    render(<CallButton phone="+14155550100" clientId="c1" />);
    await u.click(await screen.findByRole("button", { name: "Call +14155550100" }));
    expect(posted).toEqual([{ to_number: "+14155550100", client_id: "c1" }]);
    expect(await screen.findByText(/Test call placed/)).toBeInTheDocument();
  });
});
