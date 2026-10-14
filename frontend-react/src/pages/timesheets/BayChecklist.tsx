// 144cs — inline checklist for a bay job card: tick check-result items one-tap without
// opening the drawer. Reuses GET /orders/{id}/checklists and PATCH .../items/{iid}.
import { useEffect, useState } from "react";
import { api } from "../../api/client";

type Item = { id: string; label: string; field_kind: string; value: any; done: boolean; required?: boolean; section_title?: string };
type Checklist = { id: string; title?: string; items: Item[] };

const STATES: { k: string; label: string; cls: string; on: string }[] = [
  { k: "pass", label: "Pass", cls: "text-emerald-700 border-emerald-200", on: "bg-emerald-600 text-white border-emerald-600" },
  { k: "fail", label: "Fail", cls: "text-rose-700 border-rose-200", on: "bg-rose-600 text-white border-rose-600" },
  { k: "caution", label: "Caution", cls: "text-amber-700 border-amber-200", on: "bg-amber-500 text-white border-amber-500" },
];

export default function BayChecklist({ orderId, onChange }: { orderId: string; onChange: () => void }) {
  const [lists, setLists] = useState<Checklist[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.get<{ items?: any[] } | any[]>(`/orders/${orderId}/checklists`);
      const cls = (Array.isArray(r) ? r : r.items) || [];
      // each list summary lacks items — fetch each list's items
      const full = await Promise.all(cls.map((c: any) =>
        api.get<{ items: Item[]; title?: string }>(`/orders/${orderId}/checklists/${c.id}`)
          .then((d) => ({ id: c.id, title: d.title || c.title, items: d.items || [] }))));
      setLists(full);
    } catch { setLists([]); }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId]);

  async function setValue(cid: string, it: Item, value: string) {
    setBusy(it.id);
    try {
      await api.patch(`/orders/${orderId}/checklists/${cid}/items/${it.id}`, { value });
      await load(); onChange();  // refresh card progress bar
    } catch { /* keep state */ } finally { setBusy(null); }
  }

  if (!lists) return <div className="text-[11px] text-ink-tertiary px-1 py-2">Loading checklist…</div>;
  if (lists.length === 0) return <div className="text-[11px] text-ink-tertiary px-1 py-2">No checklist on this job.</div>;

  return (
    <div className="space-y-2.5">
      {lists.map((cl) => (
        <div key={cl.id} className="space-y-1.5">
          {cl.title && <div className="text-[10px] font-bold uppercase tracking-wide text-ink-tertiary">{cl.title}</div>}
          {cl.items.map((it) => (
            <div key={it.id} className="flex items-center justify-between gap-2 rounded-lg bg-white border border-slate-200/80 px-2.5 py-1.5">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold text-ink-primary truncate">
                  {it.done && <span className="text-emerald-600 mr-1">✓</span>}{it.label}{it.required ? "" : <span className="text-ink-tertiary font-normal"> · optional</span>}
                </div>
                {it.section_title && <div className="text-[9px] text-ink-tertiary">{it.section_title}</div>}
              </div>
              {it.field_kind === "check_result" ? (
                <div className="flex-none flex items-center gap-1">
                  {STATES.map((s) => {
                    const active = String(it.value).toLowerCase() === s.k;
                    return (
                      <button key={s.k} disabled={busy === it.id} onClick={() => setValue(cl.id, it, s.k)}
                        className={`px-2 py-0.5 rounded-md text-[10px] font-bold border ${active ? s.on : s.cls + " bg-white hover:bg-slate-50"} disabled:opacity-50`}>
                        {s.label}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <span className="flex-none text-[10px] text-ink-tertiary italic">{it.value != null && it.value !== "" ? "filled · open to edit" : "open job to fill"}</span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
