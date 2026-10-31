// ii144c — the stocktake count sheet on the client: read the CSV the counter filled in (exported by
// /stocktake/{id}/export/csv, or the shop's own sheet) into rows for /stocktake/{id}/import.

/** RFC 4180-ish CSV: quoted fields, doubled quotes, CRLF, a BOM; ";" or tab sheets from Excel too. */
export function parseCsv(text: string): string[][] {
  const src = text.replace(/^﻿/, "");
  const first = src.split(/\r?\n/, 1)[0] || "";
  const delim = [",", ";", "\t"].sort((a, b) => first.split(b).length - first.split(a).length)[0];
  const out: string[][] = [];
  let row: string[] = [], cell = "", quoted = false;
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"' && src[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && cell === "") quoted = true;
    else if (ch === delim) { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && src[i + 1] === "\n") i++;
      row.push(cell); out.push(row); row = []; cell = "";
    } else cell += ch;
  }
  if (cell !== "" || row.length) { row.push(cell); out.push(row); }
  return out.filter((r) => r.some((c) => c.trim() !== ""));
}

const CODE = ["code", "sku", "barcode", "serial", "imei", "mpn", "article"];
const QTY = ["counted_qty", "counted", "count", "qty", "quantity", "actual"];
const BIN = ["bin", "bin location", "location bin"];

function find(header: string[], names: string[]): number {
  const h = header.map((c) => c.trim().toLowerCase().replace(/^'/, ""));
  for (const n of names) { const i = h.indexOf(n); if (i >= 0) return i; }
  return -1;
}

export type CountRow = { code: string; counted_qty: string; bin?: string };

/** The sheet's rows as {code, counted_qty, bin}, or an error a person can act on. A sheet the
 *  export made has a "code" column; for the shop's own sheet SKU, barcode, serial or MPN work too. */
export function countRows(text: string): { rows: CountRow[]; hasBin: boolean } | { error: string } {
  const grid = parseCsv(text);
  if (grid.length < 2) return { error: "The file has no rows to import." };
  const [header, ...body] = grid;
  const ci = find(header, CODE), qi = find(header, QTY), bi = find(header, BIN);
  if (ci < 0) return { error: "Add a column called code (or SKU, barcode, serial)." };
  if (qi < 0) return { error: "Add a column called counted_qty with what you counted." };
  const rows = body.map((r) => ({ code: (r[ci] || "").trim().replace(/^'/, ""), counted_qty: (r[qi] || "").trim(),
    ...(bi >= 0 ? { bin: (r[bi] || "").trim().replace(/^'/, "") } : {}) }));
  return { rows, hasBin: bi >= 0 };
}
