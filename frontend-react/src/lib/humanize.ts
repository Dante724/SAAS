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

// Section labels read in sentence case ("Work orders", not "Work Orders"), whatever casing a
// label arrives in from the server. Words in capitals (VAT, SMS, CSV) keep their capitals.
export function sentenceCase(s?: string | null): string {
  if (!s) return "";
  const words = String(s).trim().split(/\s+/);
  return words.map((w, i) => {
    if (w.length > 1 && w === w.toUpperCase()) return w;   // an acronym
    return i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w.charAt(0).toLowerCase() + w.slice(1);
  }).join(" ");
}
