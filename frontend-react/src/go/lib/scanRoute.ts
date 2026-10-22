// Where a scanned label goes in Tekly Go.
//
// A label is either something the server must look up (an order barcode or number, a part's
// SKU/barcode, a device serial/IMEI/VIN, a ticket's tracking link — POST /api/scan/resolve), or a
// link that already names a job (a Tekly Go link, or a web-app "/orders?open=<id>" link), which we
// open without a round trip.

export type ScanResolve =
  | { matched: true; type: "order" | "product" | "asset"; id: string; label?: string; route?: string; is_estimate?: boolean }
  | { matched: false; code?: string };

export type ScanTarget =
  | { kind: "job" | "part" | "asset"; path: string; label?: string }
  | { kind: "none"; code: string };

/** Clean up what the camera or a keyboard-wedge scanner produced. */
export function normalizeCode(raw: string): string {
  // Wedge scanners often add a trailing CR/LF or tab; some QR generators pad with spaces.
  return (raw || "").replace(/[\r\n\t]+/g, "").trim();
}

/** A scanned link that already points at a job, if any. */
export function directTarget(code: string): ScanTarget | null {
  let u: URL;
  try { u = new URL(code, "https://x.invalid"); } catch { return null; }
  // Only links (absolute, or a path) count — a bare "WO-00012" parses as a relative path too.
  if (!/^(https?:\/\/|\/)/i.test(code)) return null;
  const go = u.pathname.match(/^\/go\/(job|part|asset)\/([^/?#]+)/);
  if (go) return { kind: go[1] as "job" | "part" | "asset", path: `/${go[1]}/${go[2]}` };
  const open = u.pathname.replace(/\/$/, "") === "/orders" ? u.searchParams.get("open") : null;
  if (open) return { kind: "job", path: `/job/${encodeURIComponent(open)}` };
  return null;
}

/** Turn the server's resolve answer into a Go route. */
export function targetFor(res: ScanResolve, code: string): ScanTarget {
  if (!res || !res.matched) return { kind: "none", code };
  const id = encodeURIComponent(res.id);
  if (res.type === "order") return { kind: "job", path: `/job/${id}`, label: res.label };
  if (res.type === "product") return { kind: "part", path: `/part/${id}`, label: res.label };
  if (res.type === "asset") return { kind: "asset", path: `/asset/${id}`, label: res.label };
  return { kind: "none", code };
}

/** The whole scan → route step, with the network call injected (unit-tested). */
export async function routeScan(raw: string, resolve: (code: string) => Promise<ScanResolve>): Promise<ScanTarget> {
  const code = normalizeCode(raw);
  if (!code) return { kind: "none", code };
  const direct = directTarget(code);
  if (direct) return direct;
  return targetFor(await resolve(code), code);
}
