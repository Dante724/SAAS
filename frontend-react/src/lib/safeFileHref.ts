// A file attachment's link, only if it is really a file: an inline data: URL of a harmless type,
// or one of our own media URLs. Anything else (javascript:, a web page, an outside site) gets no
// link at all, because these render on public pages a customer — or a signed-in owner — clicks.
// (Audit 2026-10-29, M1.)
const BLOCKED = /^(text\/html|application\/xhtml\+xml|image\/svg\+xml|text\/xml|application\/xml|text\/javascript|application\/javascript|application\/ecmascript|text\/ecmascript)$/i;

export function safeFileHref(value?: string | null): string | undefined {
  const v = (value || "").trim();
  if (v.startsWith("/api/media/") && !v.includes("..")) return v;
  const m = /^data:([a-z0-9.+-]+\/[a-z0-9.+-]+)(;[a-z0-9=._-]+)*;base64,/i.exec(v);
  if (m && !BLOCKED.test(m[1])) return v;
  return undefined;
}

/** An outside link we show as a button or "Play": only http(s), never javascript: or data:. */
export function safeHttpHref(value?: string | null): string | undefined {
  const v = (value || "").trim();
  return /^https?:\/\//i.test(v) ? v : undefined;
}
