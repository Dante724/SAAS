import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BillingPage, { planCost, type BillingInfo } from "./BillingPage";
import { billingNotice, planBadge } from "../../components/Shell";
import { hasFeature, extraSeatPrompt } from "../../context/Me";

function info(over: Partial<BillingInfo> = {}): BillingInfo {
  return {
    status: "trial", plan: "pro", effective_plan: "pro", period: "month", billing_mode: "stripe",
    read_only: false, read_only_reason: null, trial_ends_at: "2026-11-03", trial_days_left: 12,
    features: ["inventory", "payroll"], limits: null, price_locked_until: null,
    usage: { team_members: 3, locations: 1, jobs_per_month: 12 }, suggested_plan: "shop",
    plans: [
      { id: "solo", name: "Solo", limits: { team_members: 2, locations: 1, jobs_per_month: 150 }, features: [] },
      { id: "shop", name: "Shop", limits: { team_members: 8, locations: null, jobs_per_month: null }, features: ["Inventory, stock and suppliers", "Time clock and timesheets"] },
      { id: "pro", name: "Pro", limits: null, features: ["Purchase orders", "Payroll and commissions"] },
    ],
    prices: {
      solo: { base: { month: 29, year: 290 } },
      shop: { base: { month: 69, year: 690 }, extra_location: { month: 49, year: 490 }, extra_seat: { month: 4, year: 40 } },
      pro: { base: { month: 129, year: 1290 }, extra_location: { month: 79, year: 790 } },
    },
    yearly_months: 10, has_subscription: false, has_billing_account: false,
    cancel_at_period_end: false, current_period_end: null, configured: true,
    ...over,
  };
}

function setup(b: BillingInfo, post = vi.fn(async (path: string) => ({ url: `https://stripe.test${path}` }))) {
  const go = vi.fn();
  render(<BillingPage load={async () => b} post={post} go={go} />);
  return { go, post, user: userEvent.setup() };
}

describe("BillingPage", () => {
  it("shows the trial, the three plans at monthly prices and which one fits", async () => {
    setup(info());
    expect(await screen.findByText("12 days left in your free trial")).toBeInTheDocument();
    const solo = screen.getByRole("region", { name: "Solo plan" });
    const shop = screen.getByRole("region", { name: "Shop plan" });
    const pro = screen.getByRole("region", { name: "Pro plan" });
    expect(within(solo).getByText("$29")).toBeInTheDocument();
    expect(within(solo).getByText("150 new jobs a month")).toBeInTheDocument();
    expect(within(solo).getByText("Tekly Go phone app for techs")).toBeInTheDocument();
    expect(within(shop).getByText("$69")).toBeInTheDocument();
    expect(within(shop).getByText("Most popular")).toBeInTheDocument();
    expect(within(shop).getByText("Fits your shop")).toBeInTheDocument();
    expect(within(shop).getByText("Everything in Solo")).toBeInTheDocument();
    expect(within(shop).getByText("Multiple locations")).toBeInTheDocument();
    expect(within(shop).getByText("8 team members, shared across locations; then $4 each")).toBeInTheDocument();
    expect(within(shop).getByText(/then \$49 for each extra one/)).toBeInTheDocument();
    expect(within(pro).getByText("$129")).toBeInTheDocument();
    expect(within(pro).getByText(/then \$79 for each extra one/)).toBeInTheDocument();
    expect(within(pro).getByText("Payroll and commissions")).toBeInTheDocument();
    expect(screen.queryByText("Manage billing")).not.toBeInTheDocument();
  });

  it("yearly billing is ten months and starts Checkout for it", async () => {
    const { user, post, go } = setup(info());
    await user.click(await screen.findByRole("radio", { name: /Yearly · 2 months free/ }));
    const shop = screen.getByRole("region", { name: "Shop plan" });
    expect(within(shop).getByText("$690")).toBeInTheDocument();
    await user.click(within(shop).getByRole("button", { name: "Choose Shop" }));
    expect(post).toHaveBeenCalledWith("/billing/checkout", { plan: "shop", period: "year" });
    expect(go).toHaveBeenCalledWith("https://stripe.test/billing/checkout");
  });

  it("prices Shop and Pro for the shop's locations and people", async () => {
    setup(info({ usage: { team_members: 10, locations: 3, jobs_per_month: 0 } }));
    const pro = await screen.findByRole("region", { name: "Pro plan" });
    expect(pro).toHaveTextContent("For your shop (3 locations, 10 people): $287 / month");
    // Shop: $69 + 2 extra locations × $49 + 2 people beyond the 8 included × $4
    expect(screen.getByRole("region", { name: "Shop plan" })).toHaveTextContent("For your shop (3 locations, 10 people): $175 / month");
    expect(planCost(info({ usage: { team_members: 1, locations: 2, jobs_per_month: 0 } }), "shop", "year")).toBe(1180);
    expect(planCost(info({ usage: { team_members: 9, locations: 1, jobs_per_month: 0 } }), "shop", "year")).toBe(730);
    expect(planCost(info({ usage: { team_members: 1, locations: 3, jobs_per_month: 0 } }), "pro", "year")).toBe(2870);
    expect(planCost(info(), "solo", "month")).toBe(29);
  });

  it("explains a read-only shop and still offers plans", async () => {
    setup(info({ read_only: true, read_only_reason: "Your free trial has ended.", trial_days_left: 0 }));
    expect(await screen.findByText("Your shop is read-only")).toBeInTheDocument();
    expect(screen.getByText(/Your free trial has ended\. You can still view and export everything/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose Solo" })).toBeEnabled();
  });

  it("a Solo subscriber sees usage, switches plan in place and opens the portal", async () => {
    const { user, post, go } = setup(info({
      status: "active", plan: "solo", effective_plan: "solo", features: [], trial_days_left: null, suggested_plan: null,
      limits: { team_members: 2, locations: 1, jobs_per_month: 150 }, usage: { team_members: 2, locations: 1, jobs_per_month: 148 },
      has_subscription: true, has_billing_account: true, current_period_end: "2026-11-20",
    }));
    expect(await screen.findByText("You're on Solo, billed monthly")).toBeInTheDocument();
    const usage = screen.getByRole("region", { name: "Usage" });
    expect(usage).toHaveTextContent("2 of 2");
    expect(usage).toHaveTextContent("148 of 150");
    expect(screen.getByRole("button", { name: "Current plan" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Switch to Shop" }));
    expect(post).toHaveBeenCalledWith("/billing/change-plan", { plan: "shop", period: "month" });
    expect(go).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Manage billing" }));
    expect(post).toHaveBeenLastCalledWith("/billing/portal");
    expect(go).toHaveBeenCalledWith("https://stripe.test/billing/portal");
  });

  it("a custom-priced shop sees its plan, managed by Tekly, and no Stripe buttons", async () => {
    setup(info({
      billing_mode: "custom", status: "active", plan: "shop", effective_plan: "shop", trial_days_left: null,
      limits: { team_members: 8, locations: null, jobs_per_month: null }, current_period_end: "2027-03-31",
      has_billing_account: true,
    }));
    expect(await screen.findByText("You're on Shop")).toBeInTheDocument();
    expect(screen.getAllByText(/Your plan is managed by Tekly\. Contact us to make changes\./).length).toBeGreaterThan(0);
    expect(screen.getByText(/Renews on/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Managed plan" })).toHaveTextContent("8 team members, shared across locations");
    expect(screen.queryByRole("button", { name: /Choose|Switch|Manage billing/ })).not.toBeInTheDocument();
  });

  it("a Shop subscriber sees extra team members as billed seats, not a limit", async () => {
    setup(info({ status: "active", plan: "shop", effective_plan: "shop", trial_days_left: null, has_subscription: true,
      limits: { team_members: 8, locations: null, jobs_per_month: null }, usage: { team_members: 10, locations: 2, jobs_per_month: 3 },
      extra_seats: 2, extra_seat_price: 4 }));
    const usage = await screen.findByRole("region", { name: "Usage" });
    expect(usage).toHaveTextContent("10 8 included + 2 extra at $4 a month");
    expect(usage).not.toHaveTextContent("Locations");   // no location cap on Shop
  });

  it("shows the founding price lock and warns that switching drops it", async () => {
    setup(info({ status: "active", plan: "shop", trial_days_left: null, has_subscription: true, has_billing_account: true,
      price_locked_until: "2099-01-01" }));
    expect(await screen.findByText(/Founding price locked until/)).toBeInTheDocument();
    expect(screen.getByText(/Switching plan or billing period moves you to today's price/)).toBeInTheDocument();
  });

  it("shows a server refusal instead of leaving", async () => {
    const post = vi.fn(async () => { throw new Error("Your shop has 3 active team members (Solo allows 2)."); });
    const { user, go } = setup(info(), post);
    await user.click(await screen.findByRole("button", { name: "Choose Solo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Solo allows 2");
    expect(go).not.toHaveBeenCalled();
  });

  it("disables buying when billing isn't switched on", async () => {
    setup(info({ configured: false }));
    expect(await screen.findByText(/Online billing isn't switched on yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose Pro" })).toBeDisabled();
  });

  it("warns about a failed payment", async () => {
    setup(info({ status: "past_due", has_subscription: true, has_billing_account: true }));
    expect(await screen.findByText("Your last payment didn't go through")).toBeInTheDocument();
  });
});

describe("plan gating helpers", () => {
  it("hasFeature follows /api/auth/me billing and hides nothing before it loads", () => {
    expect(hasFeature(null, "payroll")).toBe(true);
    expect(hasFeature({ billing: null }, "payroll")).toBe(true);
    const solo = { billing: { ...info(), plan: "solo", effective_plan: "solo", features: [] } } as any;
    expect(hasFeature(solo, "time_clock")).toBe(false);
    const shop = { billing: { ...info(), features: ["inventory", "time_clock"] } } as any;
    expect(hasFeature(shop, "time_clock")).toBe(true);
    expect(hasFeature(shop, "payroll")).toBe(false);
  });

  it("asks before an added team member is billed as an extra seat", () => {
    const shop = (n: number) => ({ billing: { ...info(), effective_plan: "shop", limits: { team_members: 8, locations: null, jobs_per_month: null },
      usage: { team_members: n, locations: 1, jobs_per_month: 0 }, extra_seat_price: 4 } }) as any;
    expect(extraSeatPrompt(shop(7))).toBeNull();
    expect(extraSeatPrompt(shop(8))).toBe("Your plan includes 8 team members. Adding another costs $4 a month, added to your next bill.");
    expect(extraSeatPrompt({ billing: { ...info(), extra_seat_price: null } } as any)).toBeNull();   // trial / Pro: free
    expect(extraSeatPrompt(null)).toBeNull();
  });

  it("the logo badge names the real plan", () => {
    expect(planBadge(null)).toBeNull();
    expect(planBadge(info() as any)).toBe("TRIAL");
    expect(planBadge(info({ status: "active", plan: "solo" }) as any)).toBe("SOLO");
    expect(planBadge(info({ status: "active", plan: "shop" }) as any)).toBe("SHOP");
    expect(planBadge(info({ read_only: true, plan: "pro" }) as any)).toBe("PRO");
  });

  it("the top-bar notice covers read-only, failed payment and the trial's last week with a suggestion", () => {
    expect(billingNotice(null)).toBeNull();
    expect(billingNotice(info({ trial_days_left: 12 }) as any)).toBeNull();
    expect(billingNotice(info({ trial_days_left: 2, suggested_plan: "solo" }) as any))
      .toMatchObject({ tone: "warn", text: "2 days left in your free trial. Solo fits your shop." });
    expect(billingNotice(info({ status: "past_due" }) as any)?.cta).toBe("Update card");
    expect(billingNotice(info({ read_only: true, read_only_reason: "Your subscription has been cancelled." }) as any))
      .toMatchObject({ tone: "bad", cta: "Choose a plan" });
  });
});
