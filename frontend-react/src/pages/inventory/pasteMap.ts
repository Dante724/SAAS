// 144bn — smart paste for bulk posting. Pure, testable column-mapping logic:
// header auto-detect → content heuristics → caller shows a correctable mapping bar.
// No UI, no network — just text in, {grid, map, rows} out, so it can be unit-tested.

export type PasteField = "code" | "mpn" | "name" | "qty" | "cost" | "markup" | "ignore";
export type PostRow = { code: string; mpn: string; name: string; qty: number; purchase_price: number; markup_pct: number };

export const FIELD_LABELS: Record<PasteField, string> = {
  code: "Code / SKU", mpn: "MPN", name: "Name", qty: "Qty", cost: "Cost", markup: "Markup %", ignore: "Ignore",
};
export const FIELD_ORDER: PasteField[] = ["code", "mpn", "name", "qty", "cost", "markup", "ignore"];

// Header synonyms per field (normalised: lowercase, punctuation stripped).
const SYN: Record<Exclude<PasteField, "ignore">, string[]> = {
  code: ["code", "sku", "part", "part#", "partno", "partnumber", "part number", "item", "item#", "itemno", "barcode", "upc", "ref"],
  // opa: the manufacturer's part number has its own product field now, not an alias of code
  mpn: ["mpn", "mfrpn", "mfr pn", "mfrpart", "mfr part", "mfr part#", "mfgpn", "mfg part", "mfg part#",
    "manufacturer part", "manufacturer part number", "manufacturerpartnumber", "manufacturer pn", "oem part", "oem part#", "oempn"],
  name: ["name", "description", "desc", "product", "productname", "item name", "title", "details"],
  qty: ["qty", "quantity", "count", "units", "unit", "onhand", "on hand", "received", "qtyreceived", "qty received", "pcs", "stock"],
  cost: ["cost", "unitcost", "unit cost", "price", "unitprice", "unit price", "buy", "buyprice", "purchase", "purchaseprice", "purchase price", "wholesale", "net", "rate"],
  markup: ["markup", "margin", "markup%", "margin%", "mkup", "uplift"],
};

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9%# ]/g, "").trim();
// A cell is numeric if it is only digits + grouping/decimal marks (and has a digit): "60.50", "42,75", "1,234.56", "10", "-5".
const isNum = (s: string) => { const t = s.trim(); return /\d/.test(t) && /^\$?\s*-?[\d.,]+%?$/.test(t); };
const hasDec = (s: string) => /[.,]\d/.test(s);
// Locale-aware: when both . and , appear, the LAST one is the decimal; a lone comma is a decimal only
// when it leaves 1-2 trailing digits (42,75), otherwise it's a thousands separator (1,234).
const toNum = (s: string): number | null => {
  let t = (s || "").replace(/[^0-9.,\-]/g, "").trim();
  if (!t) return null;
  const hasC = t.includes(","), hasD = t.includes(".");
  if (hasC && hasD) {
    t = t.lastIndexOf(",") > t.lastIndexOf(".") ? t.replace(/\./g, "").replace(",", ".") : t.replace(/,/g, "");
  } else if (hasC) {
    const parts = t.split(",");
    t = (parts.length === 2 && parts[1].length <= 2) ? parts[0] + "." + parts[1] : t.replace(/,/g, "");
  }
  const n = parseFloat(t);
  return isNaN(n) ? null : n;
};

export function parseGrid(text: string): string[][] {
  // Per-line delimiter: tab-paste from Excel is tab-delimited, so split on tab when present and leave
  // commas alone (handles "1,234" and names with commas); fall back to comma only for CSV-style lines.
  return text.split(/\r?\n/).map((l) => l.replace(/\s+$/, "")).filter((l) => l.trim().length > 0)
    .map((l) => (l.includes("\t") ? l.split("\t") : l.split(",")).map((c) => c.trim()));
}

export function headerFieldFor(cell: string): PasteField | null {
  const n = norm(cell); if (!n) return null;
  for (const f of ["code", "mpn", "name", "qty", "cost", "markup"] as const) {
    if (SYN[f].some((s) => n === s || n.replace(/ /g, "") === s.replace(/ /g, ""))) return f;
  }
  return null;
}

// A row is a header if ≥2 cells map to a field AND no cell is a bare number (data rows have numbers).
export function looksLikeHeaderRow(cells: string[]): boolean {
  const hits = cells.filter((c) => headerFieldFor(c) !== null).length;
  const anyNumeric = cells.some((c) => c && isNum(c));
  return hits >= 2 && !anyNumeric;
}

export function headerMap(cells: string[]): PasteField[] {
  const seen = new Set<PasteField>();
  return cells.map((c) => {
    const f = headerFieldFor(c);
    if (f && !seen.has(f)) { seen.add(f); return f; }
    return "ignore";
  });
}

// No header → infer each column from its data.
export function heuristicMap(grid: string[][]): PasteField[] {
  const cols = grid.reduce((m, r) => Math.max(m, r.length), 0);
  const stat = [];
  for (let c = 0; c < cols; c++) {
    const vals = grid.map((r) => (r[c] ?? "").trim()).filter((v) => v !== "");
    const n = vals.length || 1;
    stat.push({
      c,
      numeric: vals.filter(isNum).length / n,
      decimal: vals.filter(hasDec).length / n,
      spaces: vals.filter((v) => /\s/.test(v)).length / n,
      codey: vals.filter((v) => /[-/]/.test(v) || /[a-z]\d|\d[a-z]/i.test(v)).length / n,
      avgLen: vals.reduce((a, v) => a + v.length, 0) / n,
    });
  }
  const map: PasteField[] = new Array(cols).fill("ignore");
  const textCols = stat.filter((s) => s.numeric < 0.5);
  // name = the wordiest text column
  const nameCol = [...textCols].sort((a, b) => (b.spaces - a.spaces) || (b.avgLen - a.avgLen))[0];
  if (nameCol) map[nameCol.c] = "name";
  // code = a code-like text column that isn't the name column
  const codeCol = textCols.filter((s) => s.c !== nameCol?.c).sort((a, b) => b.codey - a.codey)[0];
  if (codeCol) map[codeCol.c] = "code";
  // numeric columns: most-decimal → cost, the other → qty
  const numCols = stat.filter((s) => s.numeric >= 0.5).sort((a, b) => a.c - b.c);
  const costCol = [...numCols].sort((a, b) => b.decimal - a.decimal)[0];
  if (costCol) map[costCol.c] = "cost";
  const qtyCol = numCols.filter((s) => s.c !== costCol?.c)[0];
  if (qtyCol) map[qtyCol.c] = "qty";
  return map;
}

export function gridToRows(grid: string[][], map: PasteField[]): PostRow[] {
  return grid.map((cells) => {
    const row: PostRow = { code: "", mpn: "", name: "", qty: 1, purchase_price: 0, markup_pct: 0 };
    map.forEach((f, c) => {
      const v = (cells[c] ?? "").trim(); if (f === "ignore" || !v) return;
      if (f === "code") row.code = v;
      else if (f === "mpn") row.mpn = v;
      else if (f === "name") row.name = v;
      else if (f === "qty") { const n = toNum(v); if (n !== null) row.qty = n; }
      else if (f === "cost") { const n = toNum(v); if (n !== null) row.purchase_price = n; }
      else if (f === "markup") { const n = toNum(v); if (n !== null) row.markup_pct = n; }
    });
    return row;
  }).filter((r) => r.code || r.mpn || r.name);
}

// Re-derive rows for a new column mapping while keeping any field the user hand-edited.
// A field is treated as a manual override when the current value differs from what the OLD mapping
// auto-produced for that row; everything else follows the new mapping. Rows stay index-aligned
// because the grid is unchanged (structural row add/delete clears the mapping bar in the caller).
export function mergeRemap(grid: string[][], oldMap: PasteField[], newMap: PasteField[], currentRows: PostRow[]): PostRow[] {
  const oldBase = gridToRows(grid, oldMap);
  const newBase = gridToRows(grid, newMap);
  const keys: (keyof PostRow)[] = ["code", "mpn", "name", "qty", "purchase_price", "markup_pct"];
  return newBase.map((r, i) => {
    const old = oldBase[i], cur = currentRows[i];
    if (!old || !cur) return r;
    const out = { ...r };
    keys.forEach((k) => { if (cur[k] !== old[k]) (out[k] as PostRow[typeof k]) = cur[k]; });
    return out;
  });
}

// Full pipeline: header → remembered map (same column count) → heuristics.
export function analyzePaste(text: string, remembered?: PasteField[] | null): { grid: string[][]; map: PasteField[]; hadHeader: boolean } {
  const g = parseGrid(text);
  if (g.length === 0) return { grid: [], map: [], hadHeader: false };
  if (looksLikeHeaderRow(g[0])) return { grid: g.slice(1), map: headerMap(g[0]), hadHeader: true };
  const cols = g.reduce((m, r) => Math.max(m, r.length), 0);
  if (remembered && remembered.length === cols) return { grid: g, map: remembered, hadHeader: false };
  return { grid: g, map: heuristicMap(g), hadHeader: false };
}

const MAP_KEY = "ros_posting_colmap";
export function loadRememberedMap(): PasteField[] | null {
  try { const s = localStorage.getItem(MAP_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
}
export function saveRememberedMap(map: PasteField[]): void {
  try { localStorage.setItem(MAP_KEY, JSON.stringify(map)); } catch { /* ignore */ }
}
