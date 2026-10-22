// F2: the card readers list. The whole row opens Edit; Remove lives in the "⋮" menu.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../context/Me", async (orig) => {
  const real: any = await orig();
  return { ...real, useMe: () => ({ can: () => true }) };
});
import CardReadersCard from "./CardReadersCard";

const reply = (body: unknown) => ({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const prov = (configured: boolean) => ({ configured, test_mode: true, live_refused: false });
const CFG = {
  providers: { stripe: prov(false), square: prov(false), simulated: prov(true) }, test_mode: true,
  square_sandbox_devices: [], stripe_test_registration_code: "simulated-wpe", webhook_paths: { stripe: "/s", square: "/q" },
};
let readers: any[] = [];
beforeEach(() => {
  readers = [{ id: "r1", provider: "simulated", reader_id: "sim_1", label: "Front desk", location_id: null, test_mode: true }];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/payments/terminal/config")) return reply(CFG);
    if (url.includes("/payments/terminal/readers")) return reply({ items: readers });
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("CardReadersCard", () => {
  it("opens Edit when the row is clicked, and keeps Remove in the menu", async () => {
    const u = userEvent.setup();
    render(<CardReadersCard />);
    expect(await screen.findByText("1 reader")).toBeInTheDocument();
    expect(screen.getAllByText("Test mode").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    await u.click(screen.getByRole("button", { name: "Edit Front desk" }));
    const dlg = await screen.findByRole("dialog", { name: "Edit card reader" });
    expect(dlg).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Front desk");
    await u.click(screen.getByRole("button", { name: "Cancel" }));
    await u.click(screen.getByRole("button", { name: "More actions for Front desk" }));
    expect(screen.getByRole("menuitem", { name: "Remove" })).toBeInTheDocument();
  });

  it("offers Add reader in the empty state", async () => {
    readers = [];
    render(<CardReadersCard />);
    expect(await screen.findByText(/No card readers yet/)).toBeInTheDocument();
    expect(screen.getByText("0 readers")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add reader" })).toBeInTheDocument();
  });
});
