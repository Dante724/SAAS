import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog, promptDialog } from "../../lib/dialog";
import { readAsDataUrl } from "../../lib/files";
import { Gate, useMe } from "../../context/Me";
import { money } from "./types";
import { serverDate } from "../../lib/serverTime";
import { sectionCard } from "../../components/Section";

/* Recommendations on a job: the extra work the technician thinks the customer should do.
   Staff add them here (title, price, why, urgency, photo), send them to the customer's
   tracking page, and see what the customer decided. Every item shows where it stands:
   Draft → Sent → Added to the job, or Saved for later. A phone "yes" can be recorded by
   someone allowed to (it's logged with their name). */

export type RecRow = {
  id: string; title: string; note?: string | null; kind?: string; qty?: number; unit_price?: number; line_total?: number;
  status: "draft" | "sent" | "approved" | "declined" | string; urgency?: "now" | "soon" | null;
  media_data?: string | null; order_item_id?: string | null; source?: string;
  decline_reason?: string | null; remind_after?: string | null; decided_by_name?: string | null; decided_at?: string | null;
};
type Item = { id: string; name: string };

const URGENCY_LABEL: Record<string, string> = { now: "Needs attention now", soon: "Can wait" };
const STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "bg-slate-100 text-ink-secondary" },
  sent: { label: "Waiting for the customer", cls: "bg-blue-50 text-blue-700" },
  approved: { label: "Added to the job", cls: "bg-emerald-50 text-emerald-700" },
  declined: { label: "Saved for later", cls: "bg-amber-50 text-amber-800" },
};
const fmtDay = (iso?: string | null) => serverDate(iso)?.toLocaleDateString([], { month: "short", day: "numeric" }) || "";

// Shrink a photo before upload (long edge 1400px, JPEG) so proof photos stay light.
async function shrinkPhoto(f: File): Promise<string> {
  const src = await readAsDataUrl(f);
  return new Promise((res) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, 1400 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
      res(c.toDataURL("image/jpeg", 0.82));
    };
    img.onerror = () => res(src);
    img.src = src;
  });
}

export default function RecommendationsPanel({ orderId, items, onFlash, onChanged }: {
  orderId: string; items: Item[]; onFlash: (m: string) => void; onChanged?: () => void;
}) {
  const { can } = useMe();
  const [recs, setRecs] = useState<RecRow[] | null>(null);
  const [form, setForm] = useState<null | { title: string; price: string; qty: string; kind: string; urgency: string; note: string; photo: string | null; item: string }>(null);
  const [busy, setBusy] = useState(false);
  const [enabled, setEnabled] = useState(true);

  const load = () => api.get<any>(`/orders/${orderId}/recommendations`).then((r) => setRecs(r.items || [])).catch(() => setRecs([]));
  useEffect(() => { load(); }, [orderId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<any>("/staff/public-pages").then((c) => setEnabled(c?.oe_allow_recommend !== false)).catch(() => {}); }, []);

  async function call(fn: () => Promise<unknown>, ok?: string) {
    setBusy(true);
    try { await fn(); if (ok) onFlash(ok); await load(); onChanged?.(); }
    catch (e: any) { onFlash(e?.message || "Couldn't do that"); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!form || !form.title.trim()) return;
    await call(async () => {
      await api.post(`/orders/${orderId}/recommendations`, {
        title: form.title.trim(), note: form.note.trim() || null, kind: form.kind,
        unit_price: Number(form.price) || 0, qty: Number(form.qty) || 1,
        urgency: form.urgency || null, media_data: form.photo, order_item_id: form.item || null,
      });
      setForm(null);
    }, "Recommendation saved as a draft");
  }

  const drafts = (recs || []).filter((r) => r.status === "draft");
  const order = ["draft", "sent", "declined", "approved"];
  const sorted = [...(recs || [])].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  const field = "w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm text-ink-primary outline-none focus:border-primary/50";

  return (
    <section className={sectionCard}>
      <div className="flex flex-wrap items-center gap-2 px-4 pt-4">
        <div className="min-w-0 basis-full">
          <h4 className="text-sm font-semibold text-ink-primary">Recommended to the customer</h4>
          <p className="text-xs text-ink-tertiary">Extra work they can choose to add on their tracking page.</p>
        </div>
        <div className="flex items-center gap-2">
          <Gate perm="can_recommend">
            {!form && <button onClick={() => setForm({ title: "", price: "", qty: "1", kind: "service", urgency: "", note: "", photo: null, item: "" })}
              className="inline-flex items-center gap-1.5 h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:brightness-110"><M n="add" c="text-sm" />Add</button>}
          </Gate>
          {drafts.length > 1 && (
            <Gate perm="can_publish_recommend">
              <button disabled={busy} onClick={() => call(() => api.post(`/orders/${orderId}/recommendations/publish-all`, {}), `Sent ${drafts.length} to the customer`)}
                className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:bg-slate-50">Send all {drafts.length}</button>
            </Gate>
          )}
        </div>
      </div>
      {!enabled && (
        <div className="mx-4 mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[12px] text-amber-800">Customers can't see recommendations: they're switched off in Settings › Public pages.</div>
      )}

      {form && (
        <div className="m-4 rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-2.5">
          <input autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="What do you recommend? e.g. Replace swollen battery" aria-label="Recommendation" className={field} />
          <textarea value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} rows={2} placeholder="Why, in plain words for the customer (optional)" aria-label="Why" className={`${field} resize-y`} />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <label className="block text-[11px] text-ink-tertiary">Price
              <input type="number" min="0" step="0.01" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} className={`${field} mt-0.5`} /></label>
            <label className="block text-[11px] text-ink-tertiary">Qty
              <input type="number" min="1" step="1" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} className={`${field} mt-0.5`} /></label>
            <label className="block text-[11px] text-ink-tertiary">Type
              <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} className={`${field} mt-0.5`}>
                <option value="service">Service</option><option value="product">Part / product</option><option value="custom">Other</option>
              </select></label>
            <label className="block text-[11px] text-ink-tertiary">How urgent
              <select value={form.urgency} onChange={(e) => setForm({ ...form, urgency: e.target.value })} className={`${field} mt-0.5`}>
                <option value="">Not urgent</option><option value="now">Needs attention now</option><option value="soon">Can wait until next visit</option>
              </select></label>
          </div>
          {items.length > 1 && (
            <select value={form.item} onChange={(e) => setForm({ ...form, item: e.target.value })} aria-label="Which item" className={field}>
              <option value="">For the whole job</option>{items.map((it) => <option key={it.id} value={it.id}>For {it.name}</option>)}
            </select>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {form.photo
              ? <span className="inline-flex items-center gap-2"><img src={form.photo} alt="" className="h-12 w-12 rounded-lg object-cover border border-slate-200" />
                  <button type="button" onClick={() => setForm({ ...form, photo: null })} className="text-xs font-semibold text-ink-tertiary hover:text-rose-600">Remove photo</button></span>
              : <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-dashed border-slate-300 px-3 py-1.5 text-xs font-semibold text-ink-secondary hover:bg-white">
                  <M n="photo_camera" c="text-sm" />Add a photo
                  <input type="file" accept="image/*" capture="environment" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setForm({ ...form, photo: await shrinkPhoto(f) }); }} />
                </label>}
            <div className="ml-auto flex gap-2">
              <button type="button" onClick={() => setForm(null)} className="px-3 py-1.5 rounded-lg text-xs font-semibold text-ink-secondary hover:bg-white">Cancel</button>
              <button type="button" disabled={busy || !form.title.trim()} onClick={save} className="px-3.5 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-40">Save draft</button>
            </div>
          </div>
        </div>
      )}

      <div className="px-4 pb-4 pt-3">
        {recs === null && <div className="text-xs text-ink-tertiary">Loading…</div>}
        {recs && recs.length === 0 && !form && <div className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-ink-tertiary">Nothing recommended yet. Found something else that needs doing? Add it, and the customer can choose it from their phone.</div>}
        <ul className="divide-y divide-slate-100">
          {sorted.map((r) => {
            const st = STATUS[r.status] || STATUS.draft;
            const total = r.line_total ?? (r.qty || 1) * (r.unit_price || 0);
            const itemName = r.order_item_id ? items.find((it) => it.id === r.order_item_id)?.name : null;
            return (
              <li key={r.id} className="flex gap-3 py-3">
                {r.media_data ? <img src={r.media_data} alt="" className="h-12 w-12 shrink-0 rounded-lg object-cover border border-slate-200" />
                  : <span className="grid h-12 w-12 shrink-0 place-items-center rounded-lg bg-slate-100 text-ink-tertiary"><M n={r.kind === "product" ? "inventory_2" : "build"} c="text-base" /></span>}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-semibold text-ink-primary">{r.title}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.label}</span>
                    {r.urgency && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800">{URGENCY_LABEL[r.urgency]}</span>}
                    {r.source === "checklist" && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-ink-tertiary">From checklist</span>}
                  </div>
                  {r.note && <p className="mt-0.5 text-[12px] text-ink-secondary">{r.note}</p>}
                  <p className="mt-0.5 text-[11px] text-ink-tertiary">
                    {[itemName ? `For ${itemName}` : null,
                      r.status === "approved" && r.decided_by_name ? `Confirmed by ${r.decided_by_name}${r.decided_at ? `, ${fmtDay(r.decided_at)}` : ""}` : null,
                      r.status === "declined" && r.remind_after ? `Raise again after ${fmtDay(r.remind_after)}` : null].filter(Boolean).join(" · ")}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {(r.status === "draft" || r.status === "sent") && can("can_recommend") && (
                      <select value={r.urgency || ""} disabled={busy} onChange={(e) => call(() => api.patch(`/orders/${orderId}/recommendations/${r.id}`, { urgency: e.target.value || null }))}
                        aria-label={`How urgent ${r.title} is`} className="rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-ink-secondary">
                        <option value="">Not urgent</option><option value="now">Needs attention now</option><option value="soon">Can wait</option>
                      </select>
                    )}
                    {r.status === "draft" && can("can_publish_recommend") && (
                      <button disabled={busy} onClick={() => call(() => api.post(`/orders/${orderId}/recommendations/${r.id}/publish`, {}), "Sent to the customer")}
                        className="rounded-md bg-primary px-2.5 py-1 text-[11px] font-semibold text-white hover:opacity-90">Send to customer</button>
                    )}
                    {r.status === "sent" && can("can_publish_recommend") && (
                      <button disabled={busy} onClick={() => call(() => api.post(`/orders/${orderId}/recommendations/${r.id}/unpublish`, {}), "Taken back to draft")}
                        className="rounded-md border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-ink-secondary hover:bg-slate-50">Take back</button>
                    )}
                    {(r.status === "sent" || r.status === "declined" || r.status === "draft") && can("can_approve_rec_for_customer") && (
                      <button disabled={busy} onClick={async () => {
                        const name = await promptDialog("Who said yes? (the customer's name)", { title: "Customer said yes", confirmLabel: `Add ${money(total)} to the job` });
                        if (name === null) return;
                        await call(() => api.post(`/orders/${orderId}/recommendations/${r.id}/approve-for-customer`, { how: "phone", customer_name: name || null }), `${r.title} added to the job`);
                      }} className="rounded-md border border-emerald-200 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-50">Customer said yes</button>
                    )}
                    {r.status !== "approved" && can("can_recommend") && (
                      <button disabled={busy} aria-label={`Delete ${r.title}`} onClick={async () => {
                        if (!(await confirmDialog(`Delete "${r.title}"?`, { title: "Delete recommendation", confirmLabel: "Delete", danger: true }))) return;
                        await call(() => api.del(`/orders/${orderId}/recommendations/${r.id}`), "Deleted");
                      }} className="rounded-md px-2 py-1 text-[11px] font-semibold text-ink-tertiary hover:text-rose-600">Delete</button>
                    )}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold tabular-nums text-ink-primary">{money(total)}</div>
                  {(r.qty || 1) > 1 && <div className="text-[11px] tabular-nums text-ink-tertiary">{r.qty} × {money(r.unit_price || 0)}</div>}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
