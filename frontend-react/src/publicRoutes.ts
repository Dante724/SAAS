// Which standalone public page (no session, no app shell) a path belongs to. main.tsx renders
// the matching page; anything else is the signed-in app.

export type PublicRoute =
  | { page: "track"; token: string }
  | { page: "trackLookup" }
  | { page: "book" }
  | { page: "portal"; token: string }
  | { page: "checkin"; token: string }
  | { page: "checkinStatus"; token: string }
  | { page: "auth"; path: string }
  | { page: "app" };

const AUTH_PATHS = ["/login", "/signup", "/reset-password", "/verify-email", "/accept-invite"];

const tokenAfter = (path: string, prefix: string) => path.slice(prefix.length).split("/")[0] || "";

export function publicRoute(path: string): PublicRoute {
  if (path === "/track" || path === "/track/") return { page: "trackLookup" };
  if (path.startsWith("/track/")) {
    const token = tokenAfter(path, "/track/");
    return token ? { page: "track", token } : { page: "trackLookup" };
  }
  if (path.startsWith("/book/")) return { page: "book" };
  // Customer portal: every job of one customer, behind their magic link.
  if (path.startsWith("/portal/")) return { page: "portal", token: tokenAfter(path, "/portal/") };
  // Self check-in: a point's QR (/c/<token>) and the customer's own status page (/c/s/<token>).
  if (path.startsWith("/c/s/")) return { page: "checkinStatus", token: tokenAfter(path, "/c/s/") };
  if (path.startsWith("/c/")) return { page: "checkin", token: tokenAfter(path, "/c/") };
  if (AUTH_PATHS.includes(path)) return { page: "auth", path };
  return { page: "app" };
}
