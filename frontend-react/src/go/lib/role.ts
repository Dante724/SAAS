// Which "Today" a person sees. Roles are named by each shop, so infer from what we know:
// the owner flag, a role whose landing page is My work, or a technician-sounding role name.
// The person can override it in More (stored on this phone).
import type { Me } from "../../context/Me";

export type GoRole = "tech" | "desk" | "owner";
const KEY = "go_role";

export function inferRole(me: Me | null): GoRole {
  if (!me) return "tech";
  if (me.is_owner) return "owner";
  const name = (me.role || "").toLowerCase();
  if (me.home_route === "/my-work" || /tech|engineer|mechanic|repair|bench|fitter|installer/.test(name)) return "tech";
  if (/admin|owner|manager/.test(name)) return "owner";
  return "desk";
}

export function savedRole(): GoRole | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "tech" || v === "desk" || v === "owner" ? v : null;
  } catch { return null; }
}

export function saveRole(r: GoRole | null) {
  try { if (r) localStorage.setItem(KEY, r); else localStorage.removeItem(KEY); } catch { /* ignore */ }
}

export const ROLE_LABEL: Record<GoRole, string> = { tech: "Technician", desk: "Front desk", owner: "Owner" };
