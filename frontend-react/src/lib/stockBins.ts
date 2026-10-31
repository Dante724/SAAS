// ii144c — per-bin stock on the client: the product card and the job's "take from" choice read the
// rows /inventory/parts/{id}/stock returns (one per bin, plus each location's unbinned stock).

export type BinStockRow = {
  location: string; location_id?: string; bin_id?: string | null; bin?: string | null;
  available: number; reserved?: number; value?: number | null;
};

/** "Main shop · A1", or just the location for unbinned stock. */
export function rowLabel(r: BinStockRow): string {
  return r.bin ? `${r.location} · ${r.bin}` : r.location;
}

/** One total per location (the bins added together), in first-seen order. */
export function byLocation(rows: BinStockRow[]): { location: string; location_id?: string; available: number }[] {
  const out: { location: string; location_id?: string; available: number }[] = [];
  for (const r of rows) {
    const hit = out.find((o) => (o.location_id && o.location_id === r.location_id) || (!o.location_id && o.location === r.location));
    if (hit) hit.available += r.available || 0;
    else out.push({ location: r.location, location_id: r.location_id, available: r.available || 0 });
  }
  return out;
}

/** The bins at a location that hold some of the product, fullest first. */
export function binChoices(rows: BinStockRow[], locationId?: string | null): { bin_id: string; label: string; available: number }[] {
  return rows
    .filter((r) => r.bin_id && (r.available || 0) > 0 && (!locationId || r.location_id === locationId))
    .sort((a, b) => (b.available || 0) - (a.available || 0))
    .map((r) => ({ bin_id: r.bin_id as string, label: `${r.bin} (${r.available})`, available: r.available }));
}

/** Ask which bin only when there is a real choice: two places at the location hold it. */
export function needsBinChoice(rows: BinStockRow[], locationId?: string | null): boolean {
  if (!locationId) return false;     // the job's location is unknown: let the server choose
  const bins = binChoices(rows, locationId).length;
  const loose = rows.some((r) => !r.bin_id && (r.available || 0) > 0 && (!locationId || r.location_id === locationId));
  return bins + (loose ? 1 : 0) > 1;
}
