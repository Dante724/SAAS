// How Tekly Go names itself to the server. Every API call (the shared api client, once main.tsx
// calls setClientTag, and the offline queue) sends X-Tekly-Client: go/<version>, so status changes,
// photos, messages and scans made on a phone can be told apart from the web app's (the Go shop
// pilot report, docs/pilot/tekly-go-shop-pilot.md).
export const GO_VERSION = "pwa-1";
export const GO_CLIENT = `go/${GO_VERSION}`;

/** A job opened from a printed label with the phone's own camera carries ?via=qr. Report that
 *  as a scan (the server resolves the link and logs it without the code), then drop the marker
 *  so a refresh or a shared link doesn't count again. Returns whether it was a label open. */
export function noteLabelOpen(
  href: string,
  report: (code: string) => Promise<unknown>,
  replaceUrl: (url: string) => void,
): boolean {
  let u: URL;
  try { u = new URL(href); } catch { return false; }
  if (u.searchParams.get("via") !== "qr" || !/^\/go\/job\/[^/]+/.test(u.pathname)) return false;
  u.searchParams.delete("via");
  const clean = u.pathname + (u.searchParams.toString() ? `?${u.searchParams}` : "") + u.hash;
  replaceUrl(clean);
  report(u.origin + u.pathname).catch(() => { /* best effort: never block opening the job */ });
  return true;
}
