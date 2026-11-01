import type { Me } from "../context/Me";

// Where users reach Tekly support. The server sends SUPPORT_EMAIL / SUPPORT_URL on /api/auth/me;
// before sign-in (or on an older server) fall back to the public address.
export const DEFAULT_SUPPORT_EMAIL = "hello@tekly.app";

export function supportContact(me?: Me | null): { email: string; href: string; external: boolean } {
  const email = me?.support?.email || DEFAULT_SUPPORT_EMAIL;
  const url = me?.support?.url || "";
  return /^https?:\/\//i.test(url)
    ? { email, href: url, external: true }
    : { email, href: `mailto:${email}`, external: false };
}
