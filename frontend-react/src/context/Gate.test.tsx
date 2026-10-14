// Integration: the real MeProvider → MeContext → Gate wiring. Mocks /auth/me so
// we can boot a known role and assert Gate shows/hides its children by permission.
// This is the component-level counterpart to canPerm's unit tests.
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitForElementToBeRemoved } from "@testing-library/react";
import { Gate, MeProvider } from "./Me";

const get = vi.fn();
vi.mock("../api/client", () => ({ api: { get: (...a: any[]) => get(...a) } }));
// setCurrencySymbol is called by MeProvider on boot — stub it so the test needn't
// pull in the orders module.
vi.mock("../pages/orders/types", () => ({ setCurrencySymbol: () => {} }));

beforeEach(() => get.mockReset());

function renderGate(perm: string) {
  return render(
    <MeProvider>
      <Gate perm={perm}><div>secret</div></Gate>
    </MeProvider>,
  );
}

describe("Gate", () => {
  it("owner sees gated content after boot", async () => {
    get.mockResolvedValue({ is_owner: true, permissions: {} });
    renderGate("can_view_cashflow");
    expect(await screen.findByText("secret")).toBeInTheDocument();
  });

  it("non-owner with the perm explicitly denied → content is hidden after boot", async () => {
    get.mockResolvedValue({ is_owner: false, permissions: { can_view_cashflow: false } });
    renderGate("can_view_cashflow");
    // Shown pre-boot (me null = allow), then removed once the denied me resolves.
    await waitForElementToBeRemoved(() => screen.queryByText("secret"));
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("non-owner without the key → still sees content (default-allow, the 144dy guard)", async () => {
    get.mockResolvedValue({ is_owner: false, permissions: { can_manage_settings: true } });
    renderGate("can_view_cashflow");
    expect(await screen.findByText("secret")).toBeInTheDocument();
  });
});
