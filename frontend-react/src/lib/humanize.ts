// Turn a raw enum/key ("in_use", "partially_paid", "write_off") into human text
// ("In Use", "Partially Paid", "Write Off") for display. Idempotent on already-nice
// strings. Returns "" for empty so callers that guard keep working.
export function humanize(s?: string | null): string {
  if (!s) return "";
  return String(s)
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
