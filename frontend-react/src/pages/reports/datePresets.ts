// The reports' quick date ranges ("Today", "This month"…), shared by the Reports tab and the
// Follow-ups report so both pick periods the same way. Dates are the shop's local days.
export const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const REPORT_PRESETS: [string, () => [Date, Date]][] = [
  ["Today", () => { const d = new Date(); return [d, d]; }],
  ["This week", () => { const n = new Date(); const s = new Date(n); s.setDate(n.getDate() - ((n.getDay() + 6) % 7)); return [s, n]; }],
  ["This month", () => { const n = new Date(); return [new Date(n.getFullYear(), n.getMonth(), 1), n]; }],
  ["Last month", () => { const n = new Date(); return [new Date(n.getFullYear(), n.getMonth() - 1, 1), new Date(n.getFullYear(), n.getMonth(), 0)]; }],
  ["This quarter", () => { const n = new Date(); const q0 = Math.floor(n.getMonth() / 3) * 3; return [new Date(n.getFullYear(), q0, 1), n]; }],
  ["Year to date", () => { const n = new Date(); return [new Date(n.getFullYear(), 0, 1), n]; }],
];

/** The [from, to] of a preset by its label, as YYYY-MM-DD. */
export function presetRange(label: string): [string, string] {
  const p = REPORT_PRESETS.find(([l]) => l === label) || REPORT_PRESETS[2];
  const [s, e] = p[1]();
  return [isoDay(s), isoDay(e)];
}
