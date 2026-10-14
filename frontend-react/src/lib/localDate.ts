// Local-date formatting. NEVER use `new Date().toISOString().slice(...)` for a date/time the
// user thinks of in THEIR timezone — toISOString() converts to UTC, so in any zone ahead of
// or behind UTC "today" or "now" can land on the wrong day/time (e.g. 11pm IST → the previous
// day in UTC). These build the string from the Date's LOCAL components instead.
const p2 = (n: number) => String(n).padStart(2, "0");

/** "YYYY-MM-DD" in local time. */
export function isoDate(d: Date = new Date()): string {
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
}

/** "YYYY-MM" in local time (month picker default). */
export function isoMonth(d: Date = new Date()): string {
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}`;
}

/** "YYYY-MM-DDTHH:MM" in local time — the value shape an <input type="datetime-local"> wants. */
export function isoDateTimeLocal(d: Date = new Date()): string {
  return `${isoDate(d)}T${p2(d.getHours())}:${p2(d.getMinutes())}`;
}
