// The one place that turns a server timestamp into a Date (launch B6).
//
// The API sends two kinds of time:
//  • Instants — when something happened (created_at, started_at, clock-in). Stored in UTC and sent
//    with a "Z". Older endpoints still send them without one; they are UTC all the same, so a bare
//    value is read as UTC, never as the browser's local time (that put an IST clock-in of 10:00 at
//    04:30, and the activity log 4–5 hours off).
//  • Wall-clock times — when something is booked (scheduled_for, due dates). Kept in the shop's
//    own clock with no zone, so they show exactly as stored.

const ZONED = /([zZ]|[+-]\d\d:?\d\d)$/;

/** An instant from the server (UTC, with or without "Z") as a Date. */
export function serverDate(iso?: string | null): Date | null {
  if (!iso) return null;
  const d = new Date(ZONED.test(iso) || iso.length <= 10 ? iso : iso + "Z");
  return isNaN(d.getTime()) ? null : d;
}

/** A shop wall-clock time ("2026-11-04T11:00") as a Date with those same clock numbers. */
export function wallDate(iso?: string | null): Date | null {
  if (!iso) return null;
  const d = new Date(iso.replace(ZONED, ""));
  return isNaN(d.getTime()) ? null : d;
}

/** "Oct 29, 2026" for a shop date or wall-clock time ("2026-10-29" or "2026-10-29T11:00"), the app's
 *  one date format. A bare date is read as local midnight, not UTC (which can show the day before). */
export function fmtDay(iso?: string | null): string {
  if (!iso) return "";
  const d = wallDate(iso.length <= 10 ? iso + "T00:00" : iso);
  return d ? d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "";
}

/** "Nov 4, 3:05 PM" for an instant, in the viewer's clock. Empty when missing. */
export function fmtServerTime(iso?: string | null, opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }): string {
  const d = serverDate(iso);
  return d ? d.toLocaleString([], opts) : "";
}

// Sign-up: the country decides the shop's timezone. The browser's own zone wins when it is on the
// same continent (a Los Angeles shop picking "United States" keeps Los Angeles, not New York); a
// laptop set to UTC, or one travelling abroad, gets the country's main zone instead.
export function signupTimezone(countryTz: string | undefined, browserTz: string | null): string | null {
  if (!countryTz) return browserTz;
  const area = (tz: string) => tz.split("/")[0];
  return browserTz && browserTz.includes("/") && area(browserTz) === area(countryTz) ? browserTz : countryTz;
}
