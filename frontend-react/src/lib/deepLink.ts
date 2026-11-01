// Server notifications still carry the technician-app links ("/tech#/order/<id>", "/tech#/dashboard")
// because the /tech phone app reads them. Inside the main app those pages don't exist, so map
// them to where the same thing lives here. Anything else passes through unchanged.
export function toAppPath(link?: string | null): string {
  if (!link) return "/";
  let p = link.replace(/^https?:\/\/[^/]+/, "");
  if (!p.startsWith("/")) p = "/" + p;
  const m = p.match(/^\/tech\/?#\/order\/([^/?#]+)/);
  if (m) return `/orders?open=${encodeURIComponent(m[1])}`;
  if (/^\/tech\/?(#.*)?$/.test(p)) return "/my-work";
  return p;
}
