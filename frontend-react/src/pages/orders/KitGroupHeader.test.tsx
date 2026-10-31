// A kit on the job: its lines show without the repeated "(Kit name)" suffix under the header, while
// the stored description (used by aria-labels, prints and the API) keeps it; kit actions sit in a ⋯ menu.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
// Panels around the list fetch their own data; they aren't what this test is about.
vi.mock("../memberships/WOPlan", () => ({ PlanBanner: () => null, CheckoutOffer: () => null }));
vi.mock("../../components/sense/Sense", () => ({ DeferredWork: () => null, MarginNote: () => null, ReadyWhy: () => null, ServiceDue: () => null, TogetherChips: () => null }));
vi.mock("./RecommendationsPanel", () => ({ default: () => null }));
vi.mock("../../components/CatalogPicker", () => ({ default: () => null }));

import { ServicesTab, displayName } from "./WODrawer";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => reply({})));
});
afterEach(() => vi.unstubAllGlobals());

const kitLine = {
  id: "l1", description: "Screen replacement labour (Screen Replacement Kit)", kind: "service", line_type: "labour",
  qty: 1, unit_price: 250, total: 250,
  bundle_group_id: "g1", bundle_name: "Screen Replacement Kit", bundle_print_mode: "one_line", bundle_price_locked: 300, bundle_qty: 1,
};

describe("displayName", () => {
  it("strips exactly the trailing kit name and nothing else", () => {
    expect(displayName("Screen replacement labour (Screen Replacement Kit)", "Screen Replacement Kit")).toBe("Screen replacement labour");
    expect(displayName("Screen replacement labour (Screen Replacement Kit)", undefined)).toBe("Screen replacement labour (Screen Replacement Kit)");
    expect(displayName("Glass (Other kit)", "Screen Replacement Kit")).toBe("Glass (Other kit)");
    expect(displayName(" (Kit)", "Kit")).toBe(" (Kit)");
  });
});

describe("kit group on the job", () => {
  it("shows the short name under the header, keeps the full name in Remove, and opens kit actions", async () => {
    const u = userEvent.setup();
    render(<ServicesTab orderId="o1" lines={[kitLine] as any} total={250} onFlash={() => {}} />);
    const kit = await screen.findByTestId("kit-group");
    expect(within(kit).getByRole("heading", { name: "Screen replacement labour" })).toBeTruthy();
    expect(within(kit).queryByText("Screen replacement labour (Screen Replacement Kit)")).toBeNull();
    expect(within(kit).getByRole("button", { name: "Remove Screen replacement labour (Screen Replacement Kit)" })).toBeTruthy();
    expect(within(kit).getByText(/Kit · Fixed price · 1 item · Customer sees one line/)).toBeTruthy();

    const more = within(kit).getByRole("button", { name: "Kit actions for Screen Replacement Kit" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    await u.click(more);
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Remove kit" })).toBeTruthy();
    expect(within(menu).getByRole("menuitemradio", { name: "One line" }).getAttribute("aria-checked")).toBe("true");
    expect(within(menu).getByRole("menuitem", { name: "Change price" })).toBeTruthy();
  });
});
