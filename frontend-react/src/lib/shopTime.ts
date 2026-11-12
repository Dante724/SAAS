// The shop's clock. Task due dates are stored as UTC instants (sent back with no "Z"), and a
// date alone means the end of that day in the SHOP's timezone. Screens must show and pick times on
// the shop's clock, not the browser's (an IST shop seen from a laptop on UTC was 5½ hours off).
//
// One helper set for every task screen (web and Tekly Go):
//   useShopTz()             the shop's IANA zone, loaded once from GET /staff/company
//   toShopWall(iso, tz)     a server instant as the shop's wall clock, "YYYY-MM-DDTHH:MM:SS"
//   fromShopWall(wall, tz)  a wall-clock pick ("YYYY-MM-DDTHH:MM") as ISO with the shop's offset
//   shopToday(tz)           today's date in the shop
//   fmtShop(iso, tz, opts)  an instant formatted on the shop's clock
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { serverDate } from "./serverTime";

let cached: string | null = null;
let cachedLocale: string | undefined;
let pending: Promise<string | null> | null = null;
let failed = false;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((f) => f());

const validTz = (tz?: string | null): string | null => {
  if (!tz) return null;
  try { new Intl.DateTimeFormat("en-US", { timeZone: tz }); return tz; } catch { return null; }
};

/** The shop's date and time style, from its country (GB: "Fri 7 Nov", 24-hour; US: "Fri, Nov 7",
 *  12-hour; IN: "Fri, 7 Nov", 12-hour…). Undefined until loaded: the browser's own style. */
export function localeFor(country?: string | null): string | undefined {
  const cc = (country || "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(cc)) return undefined;
  const tag = `en-${cc}`;
  try { return Intl.DateTimeFormat.supportedLocalesOf([tag]).length ? tag : "en-GB"; } catch { return undefined; }
}
export const shopLocale = (): string | undefined => cachedLocale;

/** The shop's timezone (and date style). The server uses the shop's zone, or UTC when none is
 *  set; so do we. If the shop can't be reached this resolves null and NOTHING guesses a zone:
 *  screens show a retry and don't save times (a guessed UTC put new tasks a day early in India). */
export function loadShopTz(): Promise<string | null> {
  if (cached) return Promise.resolve(cached);
  if (!pending) {
    pending = api.get<{ timezone?: string | null; country?: string | null }>("/staff/company")
      .then((c) => { cachedLocale = localeFor(c?.country); cached = validTz(c?.timezone) || "UTC"; failed = false; changed(); return cached; })
      .catch(() => { pending = null; failed = true; changed(); return null; });
  }
  return pending;
}

/** The zone already loaded, if any (for code outside React, e.g. a badge count). */
export const knownShopTz = (): string | null => cached;

/** Tests and tools: forget (or preset) the loaded zone. */
export function resetShopTz(tz: string | null = null, country: string | null = null) {
  cached = validTz(tz); cachedLocale = localeFor(country); pending = null; failed = false;
}

/** The shop's clock for a screen: the zone (null while loading or when it failed), whether the
 *  last load failed, and retry(). */
export function useShopClock(): { tz: string | null; failed: boolean; retry: () => void } {
  const [, bump] = useState(0);
  useEffect(() => {
    const on = () => bump((n) => n + 1);
    listeners.add(on);
    if (!cached && !failed) loadShopTz();
    return () => { listeners.delete(on); };
  }, []);
  return { tz: cached, failed: !cached && failed, retry: () => { failed = false; changed(); loadShopTz(); } };
}

/** The shop's timezone, or null while it loads (or if it couldn't be loaded). */
export function useShopTz(): string | null { return useShopClock().tz; }

const p2 = (n: number) => String(n).padStart(2, "0");
type Parts = { y: number; mo: number; d: number; h: number; mi: number; s: number };
const FMT = new Map<string, Intl.DateTimeFormat>();
function parts(ms: number, tz: string): Parts {
  let f = FMT.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    FMT.set(tz, f);
  }
  const o: Record<string, number> = {};
  for (const p of f.formatToParts(new Date(ms))) if (p.type !== "literal") o[p.type] = Number(p.value);
  return { y: o.year, mo: o.month, d: o.day, h: o.hour === 24 ? 0 : o.hour, mi: o.minute, s: o.second };
}
/** Minutes the shop's clock is ahead of UTC at that instant (IST = 330). */
export function offsetMinutes(ms: number, tz: string): number {
  const q = parts(ms, tz);
  return Math.round((Date.UTC(q.y, q.mo - 1, q.d, q.h, q.mi, q.s) - Math.floor(ms / 1000) * 1000) / 60000);
}

const DATE_ONLY = /^\d{4}-\d\d-\d\d$/;

/** A server instant on the shop's wall clock ("YYYY-MM-DDTHH:MM:SS"). A bare date is already a
 *  shop date and comes back as it is. */
export function toShopWall(iso: string | null | undefined, tz: string): string | null {
  if (!iso) return null;
  if (DATE_ONLY.test(iso)) return iso;
  const d = serverDate(iso);
  if (!d) return null;
  const q = parts(d.getTime(), tz);
  return `${q.y}-${p2(q.mo)}-${p2(q.d)}T${p2(q.h)}:${p2(q.mi)}:${p2(q.s)}`;
}

/** A wall-clock pick in the shop ("YYYY-MM-DDTHH:MM[:SS]") as an ISO time with the shop's UTC
 *  offset, so the server stores the right instant. A bare date passes through (end of shop day). */
export function fromShopWall(wall: string | null | undefined, tz: string): string | null {
  if (!wall) return null;
  if (DATE_ONLY.test(wall)) return wall;
  const m = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d))?/.exec(wall);
  if (!m) return null;
  const [y, mo, d, h, mi, s] = m.slice(1).map((x) => Number(x || 0));
  const guess = Date.UTC(y, mo - 1, d, h, mi, s);
  let off = offsetMinutes(guess, tz);
  const off2 = offsetMinutes(guess - off * 60000, tz);   // across a DST change, use the offset in force then
  if (off2 !== off) off = off2;
  const sign = off < 0 ? "-" : "+";
  const a = Math.abs(off);
  return `${y}-${p2(mo)}-${p2(d)}T${p2(h)}:${p2(mi)}:${p2(s)}${sign}${p2(Math.floor(a / 60))}:${p2(a % 60)}`;
}

/** Today's date in the shop, "YYYY-MM-DD". */
export function shopToday(tz: string, now: Date = new Date()): string {
  const q = parts(now.getTime(), tz);
  return `${q.y}-${p2(q.mo)}-${p2(q.d)}`;
}

/** An instant (or a shop date) formatted on the shop's clock. Empty when missing. */
export function fmtShop(iso: string | null | undefined, tz: string, opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }): string {
  if (!iso) return "";
  if (DATE_ONLY.test(iso)) { const [y, mo, d] = iso.split("-").map(Number); return new Date(y, mo - 1, d).toLocaleDateString(shopLocale() ?? [], { month: opts.month || "short", day: "numeric" }); }
  const d = serverDate(iso);
  return d ? d.toLocaleString(shopLocale() ?? [], { ...opts, timeZone: tz }) : "";
}
