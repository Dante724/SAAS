// "Labor" or "Labour": the shop's country decides (owner decision): US shops read "Labor",
// everyone else "Labour". Set once from /auth/me (like the currency symbol); until the server
// sends the country, a US-dollar shop counts as US. Stored values (line_type "labour") never change:
// only the words people read go through laborText().
let word: "Labor" | "Labour" = "Labour";

export function setLaborSpelling(country?: string | null, currencyCode?: string | null) {
  const c = (country || "").trim().toUpperCase();
  word = (c ? c === "US" : (currencyCode || "").toUpperCase() === "USD") ? "Labor" : "Labour";
}

export const laborWord = (lower = false) => (lower ? word.toLowerCase() : word);

/** Swap every "labor"/"labour" in a sentence for the shop's spelling, keeping the capital. */
export function laborText(s: string) {
  return s.replace(/\b([Ll])abou?r\b/g, (_m, l: string) => (l === "L" ? word : word.toLowerCase()));
}
