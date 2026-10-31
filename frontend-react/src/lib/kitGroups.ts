// Kits print as one line, extras still shown. On the job screen a kit's lines sit under one
// header (the kit), and the lines added outside any kit get an "Extras" label — but only when the
// job has a kit, so jobs without kits look exactly as before. The money stays on each line.

export type KitPrintMode = "itemized" | "one_line" | "one_line_includes";

export const KIT_PRINT_LABELS: { value: KitPrintMode; label: string; hint: string }[] = [
  { value: "itemized", label: "Item by item", hint: "Every part and service prints on its own line." },
  { value: "one_line", label: "One line", hint: "The customer sees the kit's name and price only." },
  { value: "one_line_includes", label: "One line and what's included", hint: "The kit's name and price, plus the names of what's in it. No part prices." },
];

export function kitPrintLabel(mode?: string | null): string {
  return (KIT_PRINT_LABELS.find((o) => o.value === mode) || KIT_PRINT_LABELS[0]).label;
}

export type KitTagged = {
  id: string; total?: number;
  bundle_group_id?: string | null; bundle_name?: string | null; bundle_print_mode?: string | null;
  bundle_price_locked?: number | null; bundle_qty?: number | null;
};

export type KitSegment<L extends KitTagged> =
  | { type: "line"; line: L }
  | { type: "extras" }
  | { type: "kit"; groupId: string; name: string; mode: KitPrintMode; locked: number | null; qty: number; total: number; lines: L[] };

const MODES = new Set(KIT_PRINT_LABELS.map((o) => o.value));

export function groupKitLines<L extends KitTagged>(lines: L[]): KitSegment<L>[] {
  const kits = new Map<string, Extract<KitSegment<L>, { type: "kit" }>>();
  const kitOrder: KitSegment<L>[] = [];
  const extras: KitSegment<L>[] = [];
  for (const line of lines) {
    const g = line.bundle_group_id;
    if (!g) { extras.push({ type: "line", line }); continue; }
    let k = kits.get(g);
    if (!k) {
      const mode = (MODES.has(line.bundle_print_mode as KitPrintMode) ? line.bundle_print_mode : "itemized") as KitPrintMode;
      k = { type: "kit", groupId: g, name: line.bundle_name || "Kit", mode, locked: line.bundle_price_locked ?? null, qty: line.bundle_qty || 1, total: 0, lines: [] };
      kits.set(g, k);
      kitOrder.push(k);
    }
    k.lines.push(line);
  }
  for (const k of kits.values()) k.total = Math.round(k.lines.reduce((s, x) => s + (x.total || 0), 0) * 100) / 100;
  if (!kitOrder.length) return extras;
  return [...kitOrder, ...(extras.length ? [{ type: "extras" } as KitSegment<L>] : []), ...extras];
}
