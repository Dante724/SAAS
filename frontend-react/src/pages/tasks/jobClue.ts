// Quick add's smart link: find a hint of a work order in what is being typed, so the screen can
// offer to link it. Only a hint: the server's job search decides whether anything matches, and
// nothing is ever linked without the person choosing it.

export type JobClue = { kind: "number" | "phone" | "name"; term: string };

/** Capitalised words that start a task, name a day or a thing: never a customer's name. */
const STOP = new Set([
  "call", "ring", "phone", "text", "email", "message", "send", "reply", "chase", "remind", "follow", "ask", "tell",
  "order", "buy", "get", "pick", "collect", "deliver", "ship", "return", "refund", "book", "confirm", "pay", "invoice", "quote",
  "test", "check", "fix", "repair", "replace", "install", "clean", "restock", "label", "diagnose", "update", "backup", "back",
  "make", "take", "bring", "move", "open", "close", "print", "note", "review", "sort", "count", "find", "look", "try", "do",
  "today", "tomorrow", "tonight", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
  "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
  "the", "and", "for", "with", "about", "from", "after", "before", "new", "old", "please", "urgent", "asap", "all", "any",
  "screen", "battery", "laptop", "tablet", "case", "charger", "cable", "part", "parts", "stock", "job", "customer", "client",
]);

/** The most telling clue in `text`, or null: a job number (WO-1042, #1042, 1042: 3+ digits), a
 *  phone (6+ digits, spaces or dashes allowed) or a customer's name (capitalised words, 3+ letters,
 *  not a stop word, at most two in a row). */
export function findJobClue(text: string): JobClue | null {
  const s = text || "";
  // A job number with its prefix or a #: unmistakable, whatever its length.
  const tagged = /(?:\bWO[-\s]?|#)(\d{1,8})\b/i.exec(s);
  if (tagged) return { kind: "number", term: tagged[1] };
  // A phone: 6+ digits once spaces, dashes, dots and brackets are taken out ("+44 7700 900-123").
  for (const m of s.matchAll(/\+?\(?\d[\d\s\-.()]{4,}\d/g)) {
    const digits = m[0].replace(/\D/g, "");
    if (digits.length >= 6) return { kind: "phone", term: digits };
  }
  // A bare job number: 3-5 digits standing alone (not a time like 3pm or 10:30, not 2x or 3rd).
  const bare = /(?<![\d:.,])\b(\d{3,5})\b(?![:.,]?\d|\s*(?:am|pm|x|st|nd|rd|th|%|mm|gb|tb)\b)/i.exec(s);
  if (bare) return { kind: "number", term: bare[1] };
  // A name: the first run of up to two capitalised words that aren't stop words.
  const words = s.split(/\s+/).map((w) => w.replace(/[^\p{L}'-]/gu, "").replace(/'s$/i, ""));
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    if (!isName(w)) continue;
    const next = words[i + 1];
    return { kind: "name", term: next && isName(next) ? `${w} ${next}` : w };
  }
  return null;
}

const isName = (w: string | undefined): w is string =>
  !!w && w.length >= 3 && /^\p{Lu}\p{Ll}/u.test(w) && !STOP.has(w.toLowerCase());

/** Does a found job really answer the clue? (The server's search also matches devices; a name
 *  clue only counts when it is in the client's name.) */
export function answersClue(clue: JobClue, job: { client_name?: string | null }): boolean {
  if (clue.kind !== "name") return true;
  const name = (job.client_name || "").toLowerCase();
  return clue.term.toLowerCase().split(" ").every((w) => name.includes(w));
}

/** "awaiting_parts" -> "Awaiting parts", for a muted status pill. */
export const statusText = (key?: string | null): string => {
  const t = (key || "").replace(/[_-]+/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1).toLowerCase() : "";
};
