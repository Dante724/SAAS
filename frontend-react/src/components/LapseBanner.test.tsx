import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { BillingBanner, lapseNotice } from "./Shell";
import type { Me } from "../context/Me";

// Owner decision 7: a lapsed term (sub_end passed) keeps a week's grace; only the owner is asked
// to renew, and the banner links to the existing billing page.
const lapsed = { lapsed: true, sub_end: "2026-11-10", grace_ends_at: "2026-11-17" };
const running = { lapsed: false, sub_end: "2027-01-01", grace_ends_at: null };
const owner = (subscription: Me["subscription"]): Me => ({ is_owner: true, is_shop_owner: true, subscription });
const staff = (subscription: Me["subscription"]): Me => ({ is_owner: false, is_shop_owner: false, role: "Tech", subscription });

describe("renew banner for a lapsed shop", () => {
  it("shows the owner in grace when the term ended and how long service lasts", () => {
    render(<BillingBanner me={owner(lapsed)} />);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/settings?section=billing");
    expect(link.textContent).toContain("Your Tekly subscription ended on Nov 10, 2026.");
    expect(link.textContent).toContain("Renew by Nov 17, 2026");
    expect(link.textContent).toContain("Renew now");
  });

  it("is not shown to staff, even while the shop is lapsed", () => {
    expect(lapseNotice(staff(lapsed))).toBeNull();
    const { container } = render(<BillingBanner me={staff(lapsed)} />);
    expect(container.textContent).not.toContain("subscription ended");
  });

  it("is not shown for a shop whose term is still running or unset", () => {
    expect(lapseNotice(owner(running))).toBeNull();
    expect(lapseNotice(owner(undefined))).toBeNull();
    expect(lapseNotice(null)).toBeNull();
    const { container } = render(<BillingBanner me={owner(running)} />);
    expect(container.textContent).toBe("");
  });

  it("the other billing notices still show when the shop isn't lapsed", () => {
    render(<BillingBanner me={{ ...staff(running), billing: { status: "past_due", plan: "shop", effective_plan: "shop",
      period: "month", read_only: false, features: [] } }} />);
    expect(screen.getByRole("link").textContent).toContain("Your last Tekly payment didn't go through.");
  });
});
