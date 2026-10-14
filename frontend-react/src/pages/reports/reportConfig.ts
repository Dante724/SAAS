// The custom-report config spec, built from the builder's UI state. Pure + exported
// so the columns-vs-group-by contract is unit-tested (code-review #1–#3): columns and
// grouping are mutually exclusive — when a group_by is set the backend ignores columns
// (services/custom_reports.py), so we must NOT send them, or a saved grouped report
// carries a meaningless column list.
export type Filter = { field: string; op: string; value: string };
export type Agg = { fn: string; field: string };

export type ReportConfig = {
  source: string;
  columns?: string[];
  filters: Filter[];
  group_by?: string;
  aggregates?: ({ fn: string } | { fn: string; field: string })[];
};

// Choose which columns a result's bar chart should use: a label column and a numeric value
// column. The value column must NOT be the label column — otherwise grouping by a numeric
// field (e.g. Discount) + Count, where both columns are numbers, would plot the group key
// against itself instead of the count. Pick the label first (first non-numeric, else col 0),
// then the first numeric column that isn't the label.
export function pickChartColumns(columns: unknown[], rows: any[][]): { labelIdx: number; valueIdx: number } {
  const isNum = (i: number) => rows.some((r) => typeof r[i] === "number");
  const firstText = columns.findIndex((_c, i) => !isNum(i));
  const labelIdx = firstText >= 0 ? firstText : 0;
  const valueIdx = columns.findIndex((_c, i) => i !== labelIdx && isNum(i));
  return { labelIdx, valueIdx };
}

export function buildReportConfig(ui: {
  source: string; columns: string[]; filters: Filter[]; groupBy: string; aggregates: Agg[];
}): ReportConfig {
  const grouping = !!ui.groupBy;
  return {
    source: ui.source,
    // flat mode lists columns; grouped mode is driven by group_by + aggregates only.
    columns: grouping ? undefined : ui.columns,
    filters: ui.filters.filter((f) => f.field && f.op),
    group_by: ui.groupBy || undefined,
    aggregates: grouping
      ? ui.aggregates
          .filter((a) => a.fn === "count" || a.field)
          .map((a) => (a.fn === "count" ? { fn: "count" } : { fn: a.fn, field: a.field }))
      : undefined,
  };
}
