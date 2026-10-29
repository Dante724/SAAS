import { useEffect, useMemo, useRef, useState } from "react";
import { tint } from "../lib/tint";
import { M } from "./Shell";

// 144ik/il — status UI, split into two pieces so neither is cramped:
//   <StatusStepper>  — full-width lifecycle band that sits on its own row
//   <StatusControl>  — the compact pill + live SLA + smart picker for the header
// One component drives work orders AND estimates; the record_type it's handed
// decides the statuses, colours and hints.

export type FullStatus = {
  id: string; key: string; name: string; color?: string; status_group?: string;
  sort_order?: number; is_default?: boolean; is_terminal?: boolean;
  triggers_invoice?: boolean; triggers_notification?: boolean; triggers_payment?: boolean;
  locks_edits?: boolean; converts_to_order?: boolean; shows_accept?: boolean;
  time_limit_min?: number | null; moves_asset_to?: string | null;
  require_comment?: boolean; require_payment?: boolean; require_checklist?: boolean;
};

const GROUP_ORDER = ["new", "in_progress", "pending", "ready", "closed", "dropped"];
const GROUP_LABEL: Record<string, string> = {
  new: "New", in_progress: "In progress", pending: "Pending",
  ready: "Ready", closed: "Closed", dropped: "Dropped",
};
const DEFAULT_COLOR = "#64748b";
const tintBg = (hex?: string) => `${hex || DEFAULT_COLOR}1f`;
const tintBorder = (hex?: string) => `${hex || DEFAULT_COLOR}59`;

const fmtDur = (min?: number | null) => {
  if (min == null) return "";
  if (min < 60) return `${Math.round(min)}m`;
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return m ? `${h}h ${m}m` : `${h}h`;
};

function hint(s: FullStatus, isEstimate?: boolean): string {
  const parts: string[] = [];
  if (s.converts_to_order) parts.push("converts to work order");
  if (s.triggers_invoice) parts.push("invoices");
  if (s.triggers_payment) parts.push("needs payment");
  if (s.shows_accept) parts.push(isEstimate ? "client sign-off" : "client accept");
  if (s.moves_asset_to) parts.push("moves asset");
  if (s.locks_edits) parts.push("locks edits");
  if (!parts.length && s.time_limit_min) parts.push(`SLA ${fmtDur(s.time_limit_min)}`);
  return parts.slice(0, 2).join(" · ");
}

// ── full-width lifecycle band ──────────────────────────────────────────────
export function StatusStepper({ statuses, currentKey }: { statuses: FullStatus[]; currentKey?: string }) {
  const ordered = useMemo(
    () => [...statuses].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)),
    [statuses],
  );
  const cur = ordered.find((s) => s.key === currentKey) || ordered.find((s) => s.is_default) || ordered[0];
  const curGroupIdx = GROUP_ORDER.indexOf(cur?.status_group || "new");
  const groups = GROUP_ORDER.filter((g) => g !== "dropped" && ordered.some((s) => s.status_group === g));
  const groupColor = (g: string) => ordered.find((s) => s.status_group === g)?.color || DEFAULT_COLOR;
  if (groups.length < 2) return null;

  return (
    <div className="flex w-full select-none">
      {groups.map((g, i) => {
        const gi = GROUP_ORDER.indexOf(g);
        const done = gi < curGroupIdx;
        const current = gi === curGroupIdx;
        const col = groupColor(g);
        const first = i === 0, last = i === groups.length - 1;
        return (
          <div key={g} className="flex-1 flex flex-col items-center">
            <div className="relative flex items-center justify-center w-full h-5">
              {!first && <span className="absolute left-0 top-1/2 -translate-y-1/2 h-[2px] rounded"
                style={{ width: "50%", background: gi <= curGroupIdx ? col : "#e2e8f0" }} />}
              {!last && <span className="absolute right-0 top-1/2 -translate-y-1/2 h-[2px] rounded"
                style={{ width: "50%", background: gi < curGroupIdx ? groupColor(GROUP_ORDER[gi + 1] || g) : "#e2e8f0" }} />}
              <span className="relative z-10 rounded-full flex items-center justify-center transition-all"
                style={current
                  ? { width: 18, height: 18, background: tint(col, 15), border: `2px solid ${col}` }
                  : done
                  ? { width: 16, height: 16, background: col, color: "#fff" }
                  : { width: 12, height: 12, background: "var(--surface-2, #fff)", border: "1.5px solid #cbd5e1" }}>
                {done && <M n="check" c="text-[11px] leading-none" />}
                {current && <span className="rounded-full" style={{ width: 6, height: 6, background: col }} />}
              </span>
            </div>
            <span className="mt-1.5 text-[11px] leading-none whitespace-nowrap"
              style={{ color: current ? "var(--ink-primary, #0f172a)" : done ? "var(--ink-secondary, #475569)" : "#94a3b8", fontWeight: current ? 500 : 400 }}>
              {GROUP_LABEL[g]}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── compact pill + SLA + smart picker ──────────────────────────────────────
export default function StatusControl({
  statuses, currentKey, elapsedMin, isEstimate, onChange,
}: {
  statuses: FullStatus[]; currentKey?: string; elapsedMin?: number | null;
  isEstimate?: boolean; onChange: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const ordered = useMemo(
    () => [...statuses].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)),
    [statuses],
  );
  const cur = ordered.find((s) => s.key === currentKey) || ordered.find((s) => s.is_default) || ordered[0];
  const curIdx = ordered.findIndex((s) => s.id === cur?.id);
  const nextRec = ordered.slice(curIdx + 1).find((s) => !["closed", "dropped"].includes(s.status_group || ""));

  const overdue = cur?.time_limit_min && elapsedMin != null && elapsedMin > cur.time_limit_min;
  const nearing = cur?.time_limit_min && elapsedMin != null && !overdue && elapsedMin > cur.time_limit_min * 0.75;

  const filtered = q ? ordered.filter((s) => s.name.toLowerCase().includes(q.toLowerCase())) : ordered;
  const grouped = GROUP_ORDER
    .map((g) => ({ g, items: filtered.filter((s) => s.status_group === g && s.id !== nextRec?.id) }))
    .filter((x) => x.items.length);

  const Row = ({ s, recommended }: { s: FullStatus; recommended?: boolean }) => {
    const h = hint(s, isEstimate);
    return (
      <button
        onClick={() => { onChange(s.key); setOpen(false); setQ(""); }}
        className={`w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-left transition-colors ${recommended ? "" : "hover:bg-slate-50"}`}
        style={recommended ? { background: tintBg(s.color), border: `0.5px solid ${tintBorder(s.color)}` } : undefined}
      >
        <span className="flex items-center gap-2 min-w-0">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: s.color || DEFAULT_COLOR }} />
          <span className="text-xs font-semibold text-ink-primary truncate">{s.name}</span>
          {recommended && <span className="text-[11px] font-bold " style={{ color: s.color || DEFAULT_COLOR }}>next</span>}
        </span>
        {h && <span className="text-[11px] text-ink-tertiary whitespace-nowrap flex-shrink-0">{h}</span>}
      </button>
    );
  };

  return (
    <div className="relative flex items-center gap-2" ref={wrap}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 text-xs font-semibold py-1 pl-2.5 pr-2 rounded-full border shadow-2xs cursor-pointer focus:outline-none"
        style={{ background: tintBg(cur?.color), borderColor: tintBorder(cur?.color), color: cur?.color || DEFAULT_COLOR }}
      >
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: cur?.color || DEFAULT_COLOR }} />
        {cur?.name || "—"}
        <M n="expand_more" c="text-sm -mr-0.5 opacity-80" />
      </button>

      {cur?.time_limit_min ? (
        <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${overdue ? "text-rose-700" : nearing ? "text-amber-700" : "text-ink-tertiary"}`}>
          <M n="timer" c="text-xs" />{fmtDur(elapsedMin)} · SLA {fmtDur(cur.time_limit_min)}
        </span>
      ) : null}

      {open && (
        <div className="absolute z-30 top-8 left-0 w-72 bg-white rounded-xl border border-slate-200 shadow-clay-drawer p-2 animate-[fadeIn_.12s_ease]">
          <style>{`@keyframes fadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}`}</style>
          <div className="relative mb-1.5">
            <M n="search" c="absolute left-2 top-1.5 text-sm text-ink-tertiary" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter statuses"
              className="w-full text-xs pl-7 pr-2 py-1.5 rounded-lg border border-slate-200 focus:outline-none focus:border-primary/50" />
          </div>
          <div className="max-h-72 overflow-y-auto space-y-0.5">
            {nextRec && !q && <Row s={nextRec} recommended />}
            {grouped.map(({ g, items }) => (
              <div key={g}>
                <p className="text-[11px] font-bold text-ink-tertiary px-2.5 pt-2 pb-0.5">{GROUP_LABEL[g]}</p>
                {items.map((s) => <Row key={s.id} s={s} />)}
              </div>
            ))}
            {!filtered.length && <p className="text-xs text-ink-tertiary px-2.5 py-3 text-center">No match</p>}
          </div>
        </div>
      )}
    </div>
  );
}
