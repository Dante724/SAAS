import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog, promptDialog } from "../../lib/dialog";
import { readAsDataUrl } from "../../lib/files";
import { Gate, useMe } from "../../context/Me";
import { currencySymbol, money } from "./types";
import { flashDelivery, type Flash } from "../../lib/delivery";
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

const URGENCY_LABEL: Record<string, string> = { now: "Needs it now", soon: "Soon" };
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

// A small pill toggle for a few short choices (type, urgency) instead of a dropdown.
function Segmented({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: { v: string; t: string; dot?: string }[];
}) {
  return (
    <div role="radiogroup" aria-label={label}>
      <span className="mb-1 block text-[11px] font-medium text-ink-tertiary">{label}</span>
      <span className="inline-flex h-9 items-center gap-0.5 rounded-xl bg-slate-100 p-0.5">
        {options.map((o) => (
          <button key={o.v} type="button" role="radio" aria-checked={value === o.v} onClick={() => onChange(o.v)}
            className={`inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-[10px] px-2.5 text-xs font-medium transition ${value === o.v ? "bg-white text-ink-primary shadow-sm ring-1 ring-slate-200/70" : "text-ink-secondary hover:text-ink-primary"}`}>
            {o.dot && <span className={`h-1.5 w-1.5 rounded-full ${o.dot}`} />}{o.t}
          </button>
        ))}
      </span>
    </div>
  );
}

export default function RecommendationsPanel({ orderId, items, onFlash, onChanged }: {
  orderId: string; items: Item[]; onFlash: Flash; onChanged?: () => void;
}) {
  const { can } = useMe();
  const [recs, setRecs] = useState<RecRow[] | null>(null);
  const [form, setForm] = useState<null | { title: string; price: string; qty: string; kind: string; urgency: string; note: string; photo: string | null; item: string }>(null);
  const [busy, setBusy] = useState(false);
  const [enabled, setEnabled] = useState(true);

  const load = () => api.get<any>(`/orders/${orderId}/recommendations`).then((r) => setRecs(r.items || [])).catch(() => setRecs([]));
  useEffect(() => { load(); }, [orderId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<any>("/staff/public-pages").then((c) => setEnabled(c?.oe_allow_recommend !== false)).catch(() => { /* silent: defaults to enabled */ }); }, []);

  // ok: a fixed sentence, or (for sends) read the server's delivery result, so the screen never
  // says "sent" when email/text isn't set up or the customer has no contact.
  async function call(fn: () => Promise<any>, ok?: string | ((r: any) => void)) {
    setBusy(true);
    try { const r = await fn(); if (typeof ok === "function") ok(r); else if (ok) onFlash(ok); await load(); onChanged?.(); }
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
              <button disabled={busy} onClick={() => call(() => api.post(`/orders/${orderId}/recommendations/publish-all`, {}), (r) => flashDelivery(onFlash, r?.delivery, `${Array.isArray(r?.published) ? r.published.length : (r?.published ?? drafts.length)} shown on the customer's tracking page`))}
                className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:bg-slate-50">Send all {drafts.length}</button>
            </Gate>
          )}
        </div>
      </div>
      {!enabled && (
        <div className="mx-4 mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[12px] text-amber-800">Customers can't see recommendations: they're switched off in Settings › Public pages.</div>
      )}

      {form && (() => {
        const total = (Number(form.price) || 0) * (Math.max(1, Number(form.qty) || 1));
        const photoInput = <input type="file" accept="image/*" capture="environment" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) { const photo = await shrinkPhoto(f); setForm((cur) => cur && { ...cur, photo }); } }} />;
        return (
          <div className="mx-4 mt-4 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04),0_8px_24px_-12px_rgba(15,23,42,0.12)] focus-within:border-primary/40">
            {/* What and why read like a note to the customer, with the photo as proof beside it. */}
            <div className="flex gap-4 px-5 pt-5">
              <div className="min-w-0 flex-1">
                <input autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Replace the swollen battery" aria-label="Recommendation"
                  className="w-full bg-transparent text-base font-semibold text-ink-primary placeholder:text-ink-tertiary/70 placeholder:font-medium outline-none" />
                <textarea value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} rows={2} placeholder="Why, in plain words for the customer. e.g. The battery is swollen and pushing on the screen." aria-label="Why"
                  className="mt-1.5 w-full resize-none bg-transparent text-sm leading-relaxed text-ink-secondary placeholder:text-ink-tertiary/70 outline-none" />
              </div>
              {form.photo
                ? <div className="relative h-20 w-20 shrink-0">
                    <img src={form.photo} alt="Photo for the customer" className="h-20 w-20 rounded-xl object-cover ring-1 ring-slate-200" />
                    <button type="button" aria-label="Remove photo" onClick={() => setForm({ ...form, photo: null })}
                      className="absolute -right-1.5 -top-1.5 grid h-6 w-6 place-items-center rounded-full bg-white text-ink-secondary shadow ring-1 ring-slate-200 hover:text-rose-600"><M n="close" c="text-sm" /></button>
                  </div>
                : <label className="grid h-20 w-20 shrink-0 cursor-pointer place-content-center justify-items-center gap-1 rounded-xl border border-dashed border-slate-300 text-ink-tertiary transition hover:border-primary/40 hover:bg-slate-50 hover:text-primary">
                    <M n="photo_camera" c="text-lg" /><span className="text-[11px] font-medium">Add photo</span>{photoInput}
                  </label>}
            </div>

            <div className="mt-4 flex flex-wrap items-end gap-x-2.5 gap-y-3 border-t border-slate-100 px-5 py-4">
                <label className="block">
                  <span className="mb-1 block text-[11px] font-medium text-ink-tertiary">Price</span>
                  <span className="flex h-9 w-28 items-center rounded-xl border border-slate-200 bg-white px-3 focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/15">
                    <span className="text-sm text-ink-tertiary">{currencySymbol()}</span>
                    <input inputMode="decimal" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value.replace(/[^\d.]/g, "") })} placeholder="0" aria-label="Price"
                      className="ml-1 w-full min-w-0 bg-transparent text-sm font-medium tabular-nums text-ink-primary outline-none" />
                  </span>
                </label>
                <div>
                  <span className="mb-1 block text-[11px] font-medium text-ink-tertiary">Qty</span>
                  <span className="flex h-9 items-center rounded-xl border border-slate-200 bg-white">
                    <button type="button" aria-label="Decrease quantity" onClick={() => setForm({ ...form, qty: String(Math.max(1, (Number(form.qty) || 1) - 1)) })} className="grid h-9 w-8 place-items-center text-ink-tertiary hover:text-ink-primary"><M n="remove" c="text-sm" /></button>
                    <input inputMode="numeric" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value.replace(/\D/g, "") })} aria-label="Qty"
                      className="w-7 bg-transparent text-center text-sm font-medium tabular-nums text-ink-primary outline-none" />
                    <button type="button" aria-label="Increase quantity" onClick={() => setForm({ ...form, qty: String((Number(form.qty) || 1) + 1) })} className="grid h-9 w-8 place-items-center text-ink-tertiary hover:text-ink-primary"><M n="add" c="text-sm" /></button>
                  </span>
                </div>
                <Segmented label="Type" value={form.kind} onChange={(kind) => setForm({ ...form, kind })}
                  options={[{ v: "service", t: "Service" }, { v: "product", t: "Product" }, { v: "custom", t: "Other" }]} />
              <div className="sm:ml-auto"><Segmented label="How urgent" value={form.urgency} onChange={(urgency) => setForm({ ...form, urgency })}
                options={[{ v: "", t: "Not urgent" }, { v: "soon", t: "Soon", dot: "bg-amber-400" }, { v: "now", t: "Needs it now", dot: "bg-rose-500" }]} /></div>
              {items.length > 1 && (
                <label className="flex basis-full items-center gap-2 text-[11px] font-medium text-ink-tertiary">For
                  <select value={form.item} onChange={(e) => setForm({ ...form, item: e.target.value })} aria-label="Which item"
                    className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs font-medium text-ink-primary outline-none focus:border-primary/50">
                    <option value="">The whole job</option>{items.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}
                  </select>
                </label>
              )}
            </div>

            <div className="flex items-center gap-2 border-t border-slate-100 bg-slate-50/70 px-5 py-3">
              <div className="min-w-0 text-xs text-ink-tertiary">
                Customer sees <span className="ml-0.5 text-sm font-semibold tabular-nums text-ink-primary">{money(total)}</span>
                {(Number(form.qty) || 1) > 1 && <span className="ml-1 tabular-nums">({form.qty} × {money(Number(form.price) || 0)})</span>}
              </div>
              <div className="ml-auto flex gap-2">
                <button type="button" onClick={() => setForm(null)} className="h-9 px-3.5 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Cancel</button>
                <button type="button" disabled={busy || !form.title.trim()} onClick={save} className="h-9 px-4 rounded-xl bg-primary text-white text-xs font-semibold shadow-sm hover:brightness-110 disabled:opacity-40 disabled:shadow-none">Save draft</button>
              </div>
            </div>
          </div>
        );
      })()}

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
                    {r.urgency && <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${r.urgency === "now" ? "bg-amber-50 text-amber-800" : "bg-slate-100 text-ink-secondary"}`}>{URGENCY_LABEL[r.urgency]}</span>}
                    {r.source === "checklist" && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-ink-tertiary">From checklist</span>}
                  </div>
                  {r.note && <p className="mt-0.5 text-xs text-ink-secondary">{r.note}</p>}
                  <p className="mt-0.5 text-[11px] text-ink-tertiary">
                    {[itemName ? `For ${itemName}` : null,
                      r.status === "approved" && r.decided_by_name ? `Confirmed by ${r.decided_by_name}${r.decided_at ? `, ${fmtDay(r.decided_at)}` : ""}` : null,
                      r.status === "declined" && r.remind_after ? `Raise again after ${fmtDay(r.remind_after)}` : null].filter(Boolean).join(" · ")}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {(r.status === "draft" || r.status === "sent") && can("can_recommend") && (
                      <select value={r.urgency || ""} disabled={busy} onChange={(e) => call(() => api.patch(`/orders/${orderId}/recommendations/${r.id}`, { urgency: e.target.value || null }))}
                        aria-label={`How urgent ${r.title} is`} className="rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-ink-secondary">
                        <option value="">Not urgent</option><option value="soon">Soon</option><option value="now">Needs it now</option>
                      </select>
                    )}
                    {r.status === "draft" && can("can_publish_recommend") && (
                      <button disabled={busy} onClick={() => call(() => api.post(`/orders/${orderId}/recommendations/${r.id}/publish`, {}), (res) => flashDelivery(onFlash, res?.delivery, "Shown on the customer's tracking page"))}
                        className="h-8 rounded-lg bg-primary px-2.5 text-xs font-semibold text-white hover:brightness-110">Send to customer</button>
                    )}
                    {r.status === "sent" && can("can_publish_recommend") && (
                      <button disabled={busy} onClick={() => call(() => api.post(`/orders/${orderId}/recommendations/${r.id}/unpublish`, {}), "Taken back to draft")}
                        className="h-8 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-ink-secondary hover:bg-slate-50">Unsend</button>
                    )}
                    {(r.status === "sent" || r.status === "declined" || r.status === "draft") && can("can_approve_rec_for_customer") && (
                      <button disabled={busy} onClick={async () => {
                        const name = await promptDialog("Who said yes? (the customer's name)", { title: "Customer said yes", confirmLabel: `Add ${money(total)} to the job` });
                        if (name === null) return;
                        await call(() => api.post(`/orders/${orderId}/recommendations/${r.id}/approve-for-customer`, { how: "phone", customer_name: name || null }), `${r.title} added to the job`);
                      }} className="h-8 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-ink-primary hover:bg-slate-50">Customer said yes</button>
                    )}
                    {r.status !== "approved" && can("can_recommend") && (
                      <button disabled={busy} aria-label={`Delete ${r.title}`} onClick={async () => {
                        if (!(await confirmDialog(`Delete "${r.title}"?`, { title: "Delete recommendation", confirmLabel: "Delete", danger: true }))) return;
                        await call(() => api.del(`/orders/${orderId}/recommendations/${r.id}`), "Deleted");
                      }} className="h-8 rounded-lg px-2 text-xs font-semibold text-ink-tertiary hover:text-rose-600">Delete</button>
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
