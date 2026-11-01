import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { setCurrencySymbol } from "../pages/orders/types";
import { getLocale } from "../i18n";

// Global signed-in-user context — the React equivalent of legacy `App.me`.
// Boots once from /api/auth/me and exposes:
//   me      — the raw payload (permissions, currency, clock flags, mfa, …)
//   can()   — role permission check (deny-wins, default-allow; owner always true)
//   fmt()   — money formatting in the org currency
//   refresh — re-pull after a role/currency change so the UI updates live
export type Me = {
  id?: string; name?: string; email?: string; role?: string | null; org_name?: string | null;
  is_owner?: boolean; is_shop_owner?: boolean; permissions?: Record<string, boolean>; home_route?: string | null;
  currency?: string; currency_code?: string;
  clock_wo_enabled?: boolean; clock_attendance_enabled?: boolean;
  track_job_hours?: boolean; track_attendance?: boolean;
  mfa_enabled?: boolean; mfa_required?: boolean; email_verified?: boolean;
  pin_set?: boolean;   // Tekly Go payment PIN
  location_id?: string | null;
  billing?: Billing | null;
};

// Tekly subscription (backend services/billing.py summary). `features` lists the plan features
// unlocked right now (all of them during the trial); `limits` are the plan's caps and included team members.
export type PlanId = "solo" | "shop" | "pro";
export type ProFeature =
  // Shop and up
  | "inventory" | "labels" | "time_clock" | "reports" | "notification_rules"
  // Pro
  | "purchase_orders" | "payroll" | "multi_location" | "ai_estimates" | "accounting_sync" | "sso" | "api_keys";
export type PlanLimits = { team_members: number; locations: number | null; jobs_per_month: number | null };
export type Billing = {
  status: string; plan: PlanId; effective_plan: PlanId; period: "month" | "year";
  billing_mode?: "stripe" | "custom";
  read_only: boolean; read_only_reason?: string | null;
  trial_ends_at?: string | null; trial_days_left?: number | null;
  features: ProFeature[]; limits?: PlanLimits | null;
  usage?: { team_members: number; locations: number; jobs_per_month: number };
  suggested_plan?: PlanId | null;
  price_locked_until?: string | null;
  extra_seats?: number; extra_seat_price?: number | null;
};

// Shop (4) and Pro (8) include a pool of team members; one more is billed as an extra seat. The
// sentence to confirm before adding someone, or null when adding is free (during the trial, inside
// the pool, or on Solo, which refuses it anyway).
export function extraSeatPrompt(me: Me | null): string | null {
  const b = me?.billing;
  const cap = b?.limits?.team_members;
  if (!b || b.status === "trial" || !b.extra_seat_price || cap == null || (b.usage?.team_members ?? 0) < cap) return null;
  return `Your plan includes ${cap} team members. Adding another costs $${b.extra_seat_price} a month, added to your next bill.`;
}

type Ctx = {
  me: Me | null;
  loading: boolean;
  can: (code?: string) => boolean;
  fmt: (n?: number) => string;
  refresh: () => Promise<void>;
};

const MeContext = createContext<Ctx>({ me: null, loading: true, can: () => true, fmt: (n) => `${n ?? 0}`, refresh: async () => {} });

export function useMe() { return useContext(MeContext); }

// The permission rule itself, as a pure function so it can be unit-tested without
// mounting the provider (which boots from /api/auth/me). deny-wins, default-allow:
// no code → allowed; before boot (me null) → allowed; owner → always; otherwise a
// permission is granted unless it is explicitly false.
export function canPerm(me: Me | null, code?: string): boolean {
  if (!code) return true;
  if (!me) return true;
  if (me.is_owner || me.is_shop_owner) return true;   // the server grants the shop owner everything
  return me.permissions?.[code] !== false;
}

// Is a Pro feature unlocked for this shop? Same rule the API enforces (403 otherwise). Before boot,
// or from a server without billing, nothing is hidden — the server still has the final say.
export function hasFeature(me: Me | null, feature: ProFeature): boolean {
  if (!me?.billing) return true;
  return me.billing.features.includes(feature);
}

export function useFeature(feature: ProFeature): boolean {
  return hasFeature(useMe().me, feature);
}

// Render children only when the shop's plan includes `feature`.
export function FeatureGate({ feature, children }: { feature: ProFeature; children: ReactNode }) {
  return useFeature(feature) ? <>{children}</> : null;
}

// Render children only if the role allows `perm` — the React equivalent of legacy
// `data-perm="code"` (deny-wins). Owners/absent-me see everything.
export function Gate({ perm, children }: { perm?: string; children: ReactNode }) {
  const { can } = useMe();
  return can(perm) ? <>{children}</> : null;
}

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      const m = await api.get<Me>("/auth/me");
      setMe(m);
      if (m.currency) setCurrencySymbol(m.currency); // wire money() to the org currency
    } catch { /* keep prior me; unauthenticated flows are handled elsewhere */ }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, []);

  // deny-wins, default-allow — identical rule to legacy App.can and the API's require_perm
  const can = (code?: string) => canPerm(me, code);
  const fmt = (n?: number) => `${me?.currency || "$"}${(n ?? 0).toLocaleString(getLocale(), { maximumFractionDigits: 0 })}`;

  return <MeContext.Provider value={{ me, loading, can, fmt, refresh }}>{children}</MeContext.Provider>;
}


// Nav route → plan feature it needs (hidden on plans without it; the API refuses it anyway).
export const NAV_FEATURE: Record<string, ProFeature> = {
  "/inventory": "inventory",
  "/reports": "reports",
  "/timesheets": "time_clock",
  "/my-work": "time_clock",
};

// Nav route → permission that grants access to that area (mirrors legacy App.NAV_PERM).
export const NAV_PERM: Record<string, string> = {
  "/clients": "can_view_client_info",
  "/assets": "can_view_asset_info",
  // Same codes the API checks, so a page is never shown when its data is refused (or hidden while open).
  "/finance": "can_view_finance",
  "/invoices": "can_view_invoices",
  "/cash": "can_view_finance",
  "/reports": "can_view_reports",
  "/settings": "can_manage_settings",
};
