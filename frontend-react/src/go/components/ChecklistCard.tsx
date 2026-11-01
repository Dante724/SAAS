// The job's checklists on the phone. Two kinds live on a work order:
//   • the simple list (tick done / not done) — PATCH /orders/{id}/checklist/{item}
//   • template checklists with typed answers — PATCH /orders/{id}/checklists/{cid}/items/{item}
// Pass/caution/fail, yes/no, text, number and pick-one answers are filled here; file and other
// richer answers open the full job in Tekly. Saving is online only (a fail can draft an upsell
// on the server, which mustn't run twice from a replay).
import { useCallback, useEffect, useState } from "react";
import { Check, ExternalLink, ListChecks } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card } from "./ui";
import { haptic } from "../lib/scanner";
import { useToast } from "../lib/useGo";

type FlatItem = { id: string; label: string; required?: boolean; done: boolean };
type ClItem = { id: string; label: string; step_title?: string | null; field_kind: string; options?: string[]; required?: boolean; value?: unknown; done: boolean };
type Cl = { id: string; title?: string | null; items: ClItem[] };

const CHECK: [string, string, string][] = [["pass", "Pass", "bg-emerald-600"], ["caution", "Caution", "bg-amber-500"], ["fail", "Fail", "bg-rose-600"]];
const FILLABLE = new Set(["check_result", "yes_no", "text", "textarea", "number", "dropdown", "select"]);
const field = "mt-2 w-full min-h-[2.75rem] rounded-xl border border-slate-200 bg-white px-3 text-go-body text-ink-primary focus:outline-none focus:border-primary";

export default function ChecklistCard({ orderId, readOnly, onProgress }: { orderId: string; readOnly?: boolean; onProgress?: (left: number) => void }) {
  const { can } = useMe();
  const toast = useToast();
  const mayEdit = !readOnly && can("can_edit_checklist");
  const [flat, setFlat] = useState<FlatItem[] | null>(null);
  const [cls, setCls] = useState<Cl[] | null>(null);

  const load = useCallback(async () => {
    const id = encodeURIComponent(orderId);
    const [f, list] = await Promise.all([
      api.get<{ items: FlatItem[] }>(`/orders/${id}/checklist`).then((r) => r.items || []).catch(() => []),
      api.get<{ items: { id: string }[] }>(`/orders/${id}/checklists`).then((r) => r.items || []).catch(() => []),
    ]);
    const full = await Promise.all(list.map((c) => api.get<Cl>(`/orders/${id}/checklists/${encodeURIComponent(c.id)}`).catch(() => null)));
    setFlat(f); setCls(full.filter(Boolean) as Cl[]);
  }, [orderId]);
  useEffect(() => { load(); }, [load]);

  const all = [...(flat || []), ...(cls || []).flatMap((c) => c.items)];
  const left = all.filter((i) => i.required && !i.done).length;
  useEffect(() => { if (flat && cls) onProgress?.(left); }, [left, flat, cls]);

  const offline = () => { toast({ title: "You're offline", body: "Checklist answers need a connection.", tone: "warn" }); };

  async function tick(it: FlatItem) {
    if (!navigator.onLine) return offline();
    const done = !it.done;
    setFlat((l) => l && l.map((x) => (x.id === it.id ? { ...x, done } : x)));
    try { await api.patch(`/orders/${encodeURIComponent(orderId)}/checklist/${encodeURIComponent(it.id)}`, { done }); haptic(); }
    catch (e: any) { setFlat((l) => l && l.map((x) => (x.id === it.id ? { ...x, done: it.done } : x))); toast({ title: "Couldn't save", body: e?.message, tone: "error" }); }
  }

  async function fill(cid: string, it: ClItem, value: unknown) {
    if (!navigator.onLine) return offline();
    const prev = it;
    const blank = value === "" || value === null;
    setCls((l) => l && l.map((c) => (c.id !== cid ? c : { ...c, items: c.items.map((x) => (x.id === it.id ? { ...x, value, done: !blank } : x)) })));
    try {
      await api.patch(`/orders/${encodeURIComponent(orderId)}/checklists/${encodeURIComponent(cid)}/items/${encodeURIComponent(it.id)}`, { value });
      haptic();
    } catch (e: any) {
      setCls((l) => l && l.map((c) => (c.id !== cid ? c : { ...c, items: c.items.map((x) => (x.id === it.id ? prev : x)) })));
      toast({ title: "Couldn't save", body: e?.message, tone: "error" });
    }
  }

  if (!flat || !cls) return null;
  if (!all.length) return null;
  const doneCount = all.filter((i) => i.done).length;

  return (
    <div className="mt-4">
      <h2 className="px-4 mb-1.5 text-go-sub font-medium text-ink-tertiary flex items-center gap-1.5">
        <ListChecks size={15} />Checklist <span className="tabular-nums">{doneCount}/{all.length}</span>
        {left > 0 && <span className="ml-auto text-amber-700">{left} required left</span>}
      </h2>
      <Card className="p-2">
        {flat.map((it) => (
          <button key={it.id} onClick={() => !readOnly && tick(it)} disabled={readOnly} aria-pressed={it.done}
            className="w-full min-h-[3rem] flex items-center gap-3 rounded-xl px-2 text-left active:bg-slate-50 disabled:opacity-100">
            <span className={`h-6 w-6 shrink-0 rounded-md border-2 inline-flex items-center justify-center ${it.done ? "border-emerald-600 bg-emerald-600 text-white" : "border-slate-300"}`}>{it.done && <Check size={16} />}</span>
            <span className={`flex-1 text-go-body ${it.done ? "text-ink-secondary line-through" : "text-ink-primary"}`}>{it.label}{it.required && <span className="text-rose-600"> *</span>}</span>
          </button>
        ))}
        {cls.map((c) => (
          <div key={c.id} className="px-2 pt-2 pb-1">
            {c.title && <p className="pb-1 text-go-sub font-semibold text-ink-secondary">{c.title}</p>}
            {c.items.map((it) => {
              const v = it.value;
              return (
                <div key={it.id} className="py-2.5 border-t-[0.5px] border-slate-200 first:border-t-0">
                  <p className="text-go-body text-ink-primary">
                    {it.done && <Check size={15} className="inline mr-1 -mt-0.5 text-emerald-600" />}{it.label}{it.required && <span className="text-rose-600"> *</span>}
                  </p>
                  {!mayEdit ? (v != null && v !== "" && <p className="text-go-sub text-ink-secondary">{String(v)}</p>) : (
                    <>
                      {it.field_kind === "check_result" && (
                        <div className="mt-2 grid grid-cols-3 gap-1.5">
                          {CHECK.map(([val, lbl, on]) => (
                            <button key={val} onClick={() => fill(c.id, it, val)} aria-pressed={String(v) === val}
                              className={`min-h-[2.75rem] rounded-xl text-go-sub font-semibold ${String(v) === val ? `${on} text-white` : "border border-slate-200 bg-white text-ink-secondary"}`}>{lbl}</button>
                          ))}
                        </div>
                      )}
                      {it.field_kind === "yes_no" && (
                        <div className="mt-2 grid grid-cols-2 gap-1.5">
                          {[["yes", "Yes"], ["no", "No"]].map(([val, lbl]) => (
                            <button key={val} onClick={() => fill(c.id, it, val)} aria-pressed={String(v) === val}
                              className={`min-h-[2.75rem] rounded-xl text-go-sub font-semibold ${String(v) === val ? "bg-primary text-white" : "border border-slate-200 bg-white text-ink-secondary"}`}>{lbl}</button>
                          ))}
                        </div>
                      )}
                      {["text", "textarea", "number"].includes(it.field_kind) && (
                        <input aria-label={it.label} type={it.field_kind === "number" ? "number" : "text"} inputMode={it.field_kind === "number" ? "decimal" : undefined}
                          defaultValue={v == null ? "" : String(v)} onBlur={(e) => e.target.value !== (v == null ? "" : String(v)) && fill(c.id, it, e.target.value)}
                          className={field} placeholder="Answer" />
                      )}
                      {(it.field_kind === "dropdown" || it.field_kind === "select") && (
                        <select aria-label={it.label} defaultValue={v == null ? "" : String(v)} onChange={(e) => fill(c.id, it, e.target.value)} className={field}>
                          <option value="">Choose…</option>{(it.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      )}
                      {!FILLABLE.has(it.field_kind) && (
                        <a href={`/orders?open=${encodeURIComponent(orderId)}`} className="mt-1 inline-flex min-h-[2.75rem] items-center gap-1.5 text-go-sub font-semibold text-primary">
                          <ExternalLink size={15} />Answer in Tekly
                        </a>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </Card>
    </div>
  );
}
