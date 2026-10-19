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
  id?: string; name?: string; email?: string; role?: string | null;
  is_owner?: boolean; permissions?: Record<string, boolean>; home_route?: string | null;
  currency?: string; currency_code?: string;
  clock_wo_enabled?: boolean; clock_attendance_enabled?: boolean;
  track_job_hours?: boolean; track_attendance?: boolean;
  mfa_enabled?: boolean; mfa_required?: boolean; email_verified?: boolean;
  location_id?: string | null; org_name?: string | null;
};

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
  if (me.is_owner) return true;
  return me.permissions?.[code] !== false;
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
