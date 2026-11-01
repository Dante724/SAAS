// Import centre — pure helpers for the Import drawer (no UI, no network), so they can be unit-tested.
import { toCsv, type Cell } from "./sheet";

export type ImportKind = "products" | "services" | "clients" | "assets" | "suppliers";
export type FieldDef = { key: string; label: string; required: boolean; type: string; cost: boolean; help: string };
export type Parsed = { file_name: string; headers: string[]; rows: string[][]; row_numbers: number[]; total: number; suggested: Record<string, string> };
export type RowVerdict = { row: number; action: "create" | "update" | "skip" | "error"; label: string; matched_on: string | null; errors: string[]; warnings: string[] };

export const KIND_NOUN: Record<ImportKind, [string, string]> = {
  products: ["product", "products"], services: ["service", "services"], clients: ["client", "clients"],
  assets: ["asset", "assets"], suppliers: ["supplier", "suppliers"],
};

// Required fields nobody mapped a column to — new records would all fail without them.
export function missingRequired(fields: FieldDef[], mapping: Record<string, string>): FieldDef[] {
  const used = new Set(Object.values(mapping));
  return fields.filter((f) => f.required && !used.has(f.key));
}

// First non-blank value of each column, shown next to the column so the mapping is easy to check.
export function sampleValues(parsed: Pick<Parsed, "headers" | "rows">): string[] {
  return parsed.headers.map((_, c) => {
    for (const r of parsed.rows) { const v = (r[c] ?? "").toString().trim(); if (v) return v; }
    return "";
  });
}

// The rows that need fixing, as the shop's own columns plus "Row" and "Problem" — fix and re-upload.
export function errorsSheet(parsed: Pick<Parsed, "headers" | "rows" | "row_numbers">, verdicts: RowVerdict[]): Cell[][] {
  const bad = new Map(verdicts.filter((v) => v.action === "error").map((v) => [v.row, v.errors.join("; ")]));
  const out: Cell[][] = [["Row", ...parsed.headers, "Problem"]];
  parsed.rows.forEach((r, i) => {
    const n = parsed.row_numbers?.[i] ?? i + 2;
    const msg = bad.get(n);
    if (msg !== undefined) out.push([n, ...parsed.headers.map((_, c) => r[c] ?? ""), msg]);
  });
  return out;
}

export function errorsCsv(parsed: Pick<Parsed, "headers" | "rows" | "row_numbers">, verdicts: RowVerdict[]): string {
  return toCsv(errorsSheet(parsed, verdicts));
}

export function countBy(verdicts: RowVerdict[]): Record<RowVerdict["action"], number> {
  const c = { create: 0, update: 0, skip: 0, error: 0 };
  verdicts.forEach((v) => { c[v.action] += 1; });
  return c;
}
