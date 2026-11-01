// Where to go after signing in. Only same-site paths are accepted ("/go/today"), never another
// site ("//evil.example", "https://…", "/\\evil") — an open redirect on the sign-in page would let
// a phishing link bounce people to a look-alike after a real sign-in.
export function safeNext(search: string, fallback = "/"): string {
  let next: string | null = null;
  try { next = new URLSearchParams(search).get("next"); } catch { return fallback; }
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\") || /[\u0000-\u001f]/.test(next)) return fallback;
  return next;
}
