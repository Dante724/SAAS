import { useEffect, useState } from "react";
import { M } from "./Shell";
import FloatingMenu from "./FloatingMenu";
import { api } from "../api/client";
import { useMe } from "../context/Me";
import type { AutoPrintEvent } from "../lib/autoPrint";

// The printer button on the new-job, close-job and POS screens (Orderry's auto-print checkboxes):
// tick the documents that should print by themselves at this moment, untick to stop. Shop-wide —
// the next job or sale prints what's ticked; nothing ticked means nothing prints by itself.
type Tpl = { id: string; name: string; auto_print_events?: string[]; auto_print_allowed?: string[] };

const WHEN: Record<AutoPrintEvent, string> = {
  create: "a job is created", close: "a job is closed", sale: "a sale is made",
};

export default function AutoPrintPicker({ event }: { event: AutoPrintEvent }) {
  const { can } = useMe();
  const canEdit = can("can_manage_settings");
  const [tpls, setTpls] = useState<Tpl[] | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<{ items: Tpl[] }>("/documents/templates")
      .then((r) => { if (live) setTpls((r?.items || []).filter((t) => (t.auto_print_allowed || []).includes(event))); })
      .catch(() => { if (live) setTpls([]); });
    return () => { live = false; };
  }, [event]);

  const on = (tpls || []).filter((t) => (t.auto_print_events || []).includes(event));

  async function toggle(t: Tpl) {
    if (!canEdit || saving) return;
    const cur = t.auto_print_events || [];
    const events = cur.includes(event) ? cur.filter((e) => e !== event) : [...cur, event];
    setSaving(t.id); setErr(null);
    try {
      const r = await api.put<Tpl>(`/documents/templates/${t.id}/auto-print`, { events });
      setTpls((ts) => (ts || []).map((x) => (x.id === t.id ? { ...x, auto_print_events: r?.auto_print_events ?? events } : x)));
    } catch (e: any) {
      setErr(e?.message || "Couldn't save that");
    } finally { setSaving(null); }
  }

  const label = on.length === 0 ? "Auto-print off" : on.length === 1 ? on[0].name : `${on.length} auto-print`;
  return (
    <>
      <button type="button" data-auto-print={event} aria-label={`Print automatically when ${WHEN[event]}`} aria-expanded={open}
        onClick={() => setOpen((v) => !v)} title={`Print automatically when ${WHEN[event]}`}
        className={`text-xs font-semibold flex items-center gap-1 border rounded-lg px-2 py-1 bg-white max-w-[180px] ${on.length ? "border-primary/40 text-primary" : "border-slate-200 text-ink-tertiary hover:text-ink-primary"}`}>
        <M n={on.length ? "print" : "print_disabled"} c="text-xs" /><span className="truncate">{label}</span>
      </button>
      {open && (
        <FloatingMenu anchorSel={`[data-auto-print="${event}"]`} onClose={() => setOpen(false)} width={264}>
          <div>
            <div className="text-[11px] font-bold text-ink-tertiary px-2 py-1">Print automatically when {WHEN[event]}</div>
            {(tpls || []).map((t) => {
              const checked = (t.auto_print_events || []).includes(event);
              return (
                <label key={t.id} className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-ink-primary ${canEdit ? "hover:bg-slate-50 cursor-pointer" : "opacity-70"}`}>
                  <input type="checkbox" checked={checked} disabled={!canEdit || saving === t.id} onChange={() => toggle(t)} />
                  <span className="truncate">{t.name}</span>
                </label>
              );
            })}
            {tpls && tpls.length === 0 && (
              <p className="px-2 py-1.5 text-ink-tertiary">No documents can print here yet. Add one in Settings → Documents you print.</p>
            )}
            {tpls === null && <p className="px-2 py-1.5 text-ink-tertiary">Loading…</p>}
            <p className="px-2 pt-1.5 mt-1 border-t border-slate-100 text-[11px] text-ink-tertiary">
              {canEdit ? "Applies to the whole shop. Untick everything and nothing prints by itself."
                : "Only someone who manages settings can change this."}
            </p>
            {err && <p className="px-2 py-1 text-[11px] text-rose-600">{err}</p>}
          </div>
        </FloatingMenu>
      )}
    </>
  );
}
