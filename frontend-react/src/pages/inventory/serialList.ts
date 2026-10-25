// 16b — serial / IMEI intake. Pure helpers behind the scan box: split a paste, tidy a value the
// way the server stores it, and flag what we can tell without asking the server (a repeat in
// this delivery, a wrong IMEI check digit). The server check adds "already in stock" / "sold before".

export type SerialStatus = "ok" | "warn" | "block" | "checking";
export type SerialRow = { input: string; serial: string; kind: "imei" | "serial"; status: SerialStatus; message?: string | null };

/** A pasted block → entries. Newlines, commas, semicolons and tabs separate; spaces inside an entry stay. */
export function splitSerials(raw: string): string[] {
  return (raw || "").split(/[\r\n,;\t]+/).map((s) => s.trim()).filter(Boolean);
}

/** Same rule as the server: 15–16 digits (spaces / dashes allowed) is an IMEI, digits only;
 * anything else is a serial, upper-cased without spaces. */
export function normalizeSerial(v: string): { serial: string; kind: "imei" | "serial" } {
  const t = (v || "").trim();
  const digits = t.replace(/\D/g, "");
  if (digits && digits === t.replace(/[\s-]/g, "") && (digits.length === 15 || digits.length === 16)) return { serial: digits, kind: "imei" };
  return { serial: t.replace(/\s+/g, "").toUpperCase(), kind: "serial" };
}

export function luhnOk(digits: string): boolean {
  let total = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) { d *= 2; if (d > 9) d -= 9; }
    total += d;
  }
  return total % 10 === 0;
}

/** Local checks for the whole list, in order. A repeat is blocked; a bad IMEI checksum is a warning. */
export function localCheck(values: string[]): SerialRow[] {
  const seen = new Set<string>();
  return values.map((input) => {
    const { serial, kind } = normalizeSerial(input);
    let status: SerialStatus = "ok"; let message: string | null = null;
    if (!serial) { status = "block"; message = "Empty entry."; }
    else if (seen.has(serial)) { status = "block"; message = "Entered twice in this delivery."; }
    else if (kind === "imei" && serial.length === 15 && !luhnOk(serial)) { status = "warn"; message = "This IMEI's check digit doesn't match — probably a typo."; }
    if (serial) seen.add(serial);
    return { input, serial, kind, status, message };
  });
}

/** Merge the server's verdicts (same order as sent) over the local ones; the stricter one wins. */
export function mergeServer(local: SerialRow[], server: { status: SerialStatus; message?: string | null }[] | null): SerialRow[] {
  if (!server) return local;
  const rank: Record<SerialStatus, number> = { checking: 0, ok: 1, warn: 2, block: 3 };
  return local.map((r, i) => {
    const s = server[i];
    if (!s || rank[s.status] <= rank[r.status]) return r;
    return { ...r, status: s.status, message: s.message ?? r.message };
  });
}

/** Add scanned / pasted text to the list. Returns the new list and how many were skipped as repeats. */
export function addSerials(list: string[], raw: string): { list: string[]; skipped: number } {
  const have = new Set(list.map((v) => normalizeSerial(v).serial));
  let skipped = 0;
  const next = [...list];
  for (const v of splitSerials(raw)) {
    const n = normalizeSerial(v).serial;
    if (have.has(n)) { skipped++; continue; }
    have.add(n); next.push(v);
  }
  return { list: next, skipped };
}
