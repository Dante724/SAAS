// Pure helpers behind the Total profit view and the remembered report filters (unit-tested).

export type ProfitRow = {
  kind: "job" | "dropped" | "sale"; id: string; number: string; date: string;
  location_id?: string | null; employee_id?: string | null; location_name?: string | null;
  employee_name?: string | null; client_name?: string | null; reason?: string | null;
  revenue: number; tax: number; cogs?: number; labour_cost: number; commission: number;
  profit?: number; written_off: number;
};

export type Trend = { delta: number; pct: number | null; dir: "up" | "down" | "flat"; label: string };

// Change against the previous period. No previous figure → no percentage (never "Infinity%").
export function trend(cur: number, prev: number): Trend {
  const delta = Math.round((cur - prev) * 100) / 100;
  const pct = prev ? Math.round(((cur - prev) / Math.abs(prev)) * 1000) / 10 : null;
  const dir = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const label = dir === "flat" ? "Same as last period"
    : pct == null ? (dir === "up" ? "Up from nothing last period" : "Down from nothing last period")
    : `${dir === "up" ? "Up" : "Down"} ${Math.abs(pct)}% on last period`;
  return { delta, pct, dir, label };
}

export type Drill = { kind?: ProfitRow["kind"]; location_id?: string; employee_id?: string };

// The rows behind a clicked number: a bucket (jobs / dropped / sales), a location or a technician.
export function drillRows(rows: ProfitRow[], d: Drill): ProfitRow[] {
  return rows.filter((r) => (!d.kind || r.kind === d.kind)
    && (d.location_id === undefined || (r.location_id ?? "") === d.location_id)
    && (d.employee_id === undefined || (r.employee_id ?? "") === d.employee_id));
}

export const KIND_LABEL: Record<ProfitRow["kind"], string> = { job: "Job", dropped: "Dropped job", sale: "Counter sale" };

// ── report filters ──────────────────────────────────────────────────────────
export type ReportFilters = { movement: "with" | "without" | "all"; hideZero: boolean; status: string; typeId: string };
export const DEFAULT_FILTERS: ReportFilters = { movement: "with", hideZero: false, status: "", typeId: "" };

// Which filters each report understands (mirrors backend reports.ReportFilters).
export const REPORT_FILTERS: Record<string, (keyof ReportFilters)[]> = {
  product_turnover: ["movement"],
  wo_closed: ["hideZero"],
  dropped_jobs: ["status", "typeId"],
};

export function filterParams(report: string | null, f: ReportFilters): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of REPORT_FILTERS[report || ""] || []) {
    if (k === "movement" && f.movement !== "with") out.movement = f.movement;
    if (k === "hideZero" && f.hideZero) out.hide_zero = "true";
    if (k === "status" && f.status) out.status = f.status;
    if (k === "typeId" && f.typeId) out.type_id = f.typeId;
  }
  return out;
}

// Remembered between visits (per user, via /prefs). Anything unexpected falls back to defaults.
export const REPORTS_PREF = "listview:reports";
export type ReportsPref = { report: string | null; locId: string; filters: ReportFilters };

export function readReportsPref(raw: unknown): ReportsPref {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, any>;
  const f = (r.filters && typeof r.filters === "object" ? r.filters : {}) as Record<string, any>;
  const str = (v: unknown) => (typeof v === "string" && v.length <= 120 ? v : "");
  return {
    report: str(r.report) || null,
    locId: str(r.locId),
    filters: {
      movement: ["with", "without", "all"].includes(f.movement) ? f.movement : DEFAULT_FILTERS.movement,
      hideZero: f.hideZero === true,
      status: str(f.status),
      typeId: str(f.typeId),
    },
  };
}
