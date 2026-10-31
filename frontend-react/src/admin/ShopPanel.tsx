import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { adminApi, fmtDate, SEGMENTS, type Me, type Shop } from "./api";
import { loadFailed } from "../lib/notice";

type Action = "discount" | "trial" | "plan" | "suspend" | "unsuspend" | "assign" | null;
const input = "w-full px-3 py-2 rounded-lg border border-slate-300 text-sm";
const btn = "px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold hover:bg-slate-50";

/** One shop: details this admin may see, actions their role allows, notes and history. */
export default function ShopPanel({ id, me, onClose }: { id: string; me: Me; onClose: () => void }) {
  const can = (p: string) => !!me.permissions[p];
  const [s, setS] = useState<Shop | null>(null);
  const [err, setErr] = useState("");
  const [flash, setFlash] = useState("");
  const [action, setAction] = useState<Action>(null);
  const [admins, setAdmins] = useState<{ id: string; name: string; active: boolean }[]>([]);
  const load = useCallback(() => adminApi.get<Shop>(`/tenants/${id}`).then(setS).catch((e) => setErr(e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => {
    if (action === "assign") adminApi.get<{ items: { id: string; name: string }[] }>("/team").then((r) => setAdmins(r.items.map((a) => ({ ...a, active: true })))).catch(loadFailed("the admin team"));
  }, [action]); // eslint-disable-line react-hooks/exhaustive-deps

  async function done(p: Promise<any>, ok: string) {
    setErr("");
    try {
      const r = await p;
      setFlash(r?.status === "requested" ? `Sent to the owner for approval: ${r.why}` : ok);
      setAction(null); load();
    } catch (e: any) { setErr(e.message); }
  }

  if (!s) return <Drawer onClose={onClose}><p className="p-6 text-sm text-ink-tertiary">{err || "Loading…"}</p></Drawer>;
  const has = (k: keyof Shop) => s[k] !== undefined;
  return (
    <Drawer onClose={onClose}>
      <div className="px-6 py-5 border-b border-slate-100">
        <div className="flex items-start gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold truncate">{s.name}</h2>
            <div className="text-xs text-ink-tertiary mt-0.5">{SEGMENTS[s.segment] || s.segment} · <span className="capitalize">{s.plan}</span> plan · {s.country || "No country"}</div>
          </div>
          <button aria-label="Close" onClick={onClose} className="ml-auto text-ink-tertiary hover:text-ink-primary text-xl leading-none">×</button>
        </div>
        <button onClick={() => navigator.clipboard?.writeText(s.account_id).then(() => setFlash("Account ID copied"))}
          className="mt-3 font-mono text-sm px-2 py-1 rounded bg-slate-50 border border-slate-200" title="Copy">{s.account_id}</button>
      </div>

      <div className="px-6 py-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm border-b border-slate-100">
        <Fact label="Trial or term ends">{fmtDate(s.sub_end)}</Fact>
        <Fact label="Looked after by">{s.assigned_to || "Nobody yet"}</Fact>
        <Fact label="Staff">{s.employees}</Fact>
        <Fact label="Last contacted">{fmtDate(s.last_contacted_at)}</Fact>
        {has("jobs_7d") && <Fact label="Jobs in the last 7 days">{s.jobs_7d}</Fact>}
        {has("last_activity_at") && <Fact label="Last job">{fmtDate(s.last_activity_at)}</Fact>}
        {has("owner_email") && <Fact label="Owner">{[s.owner_name, s.owner_email].filter(Boolean).join(" · ") || "—"}</Fact>}
        {has("company_phone") && <Fact label="Shop phone">{s.company_phone || "—"}</Fact>}
        {has("discount_pct") && <Fact label="Discount">{s.discount_pct ? `${s.discount_pct}%${s.discount_until ? ` until ${fmtDate(s.discount_until)}` : ""} · ${s.discount_reason || ""}` : "None"}</Fact>}
        {s.sub_status === "suspended" && <Fact label="Suspended, shop sees">{s.suspend_message || "The standard message"}</Fact>}
      </div>

      <div className="px-6 py-4 flex flex-wrap gap-2 border-b border-slate-100">
        {can("give_discount") && <button className={btn} onClick={() => setAction("discount")}>Discount</button>}
        {can("extend_trial") && <button className={btn} onClick={() => setAction("trial")}>Extend trial</button>}
        {(can("change_plan") || can("request_plan_change")) && <button className={btn} onClick={() => setAction("plan")}>Change plan</button>}
        {can("assign_shops") && <button className={btn} onClick={() => setAction("assign")}>Hand over</button>}
        {can("suspend") && (s.sub_status === "suspended"
          ? <button className={btn} onClick={() => setAction("unsuspend")}>Unsuspend</button>
          : <button className={`${btn} text-rose-600`} onClick={() => setAction("suspend")}>Suspend</button>)}
      </div>

      {action && (
        <ActionForm action={action} shop={s} me={me} admins={admins} onCancel={() => setAction(null)}
          onSubmit={(body) => {
            const map: Record<string, [string, string]> = {
              discount: [`/tenants/${id}/discount`, "Discount saved"], trial: [`/tenants/${id}/extend-trial`, "Trial extended"],
              plan: [`/tenants/${id}/plan`, "Plan changed"], suspend: [`/tenants/${id}/suspend`, "Shop suspended"],
              unsuspend: [`/tenants/${id}/unsuspend`, "Shop unsuspended"], assign: [`/tenants/${id}/assign`, "Shop handed over"],
            };
            const [url, ok] = map[action];
            done(adminApi.post(url, body), ok);
          }} />
      )}

      {(flash || err) && <p role={err ? "alert" : "status"} className={`px-6 pt-3 text-sm ${err ? "text-rose-600" : "text-emerald-700"}`}>{err || flash}</p>}

      {has("notes") && <Notes shopId={id} notes={s.notes || []} canAdd={can("add_notes")} onAdded={load} />}

      {s.employees_detail && (
        <section className="px-6 py-4 border-t border-slate-100">
          <h3 className="text-sm font-semibold mb-2">Staff</h3>
          {s.employees_detail.map((e) => (
            <div key={e.id} className="text-sm flex gap-2 py-1"><span>{e.name}</span><span className="text-ink-tertiary">{e.email}</span>{!e.active && <span className="text-rose-600 text-xs">Inactive</span>}</div>
          ))}
        </section>
      )}

      <section className="px-6 py-4 border-t border-slate-100">
        <h3 className="text-sm font-semibold mb-2">History</h3>
        {(s.history || []).length === 0 && <p className="text-sm text-ink-tertiary">No admin changes yet.</p>}
        {(s.history || []).map((h, i) => (
          <div key={i} className="text-xs py-1.5 border-b border-slate-50 last:border-0">
            <span className="font-semibold">{h.action.replace(/_/g, " ")}</span> <span className="text-ink-tertiary">by {h.by} · {fmtDate(h.at)}</span>
            {h.reason && <div className="text-ink-secondary">{h.reason}</div>}
          </div>
        ))}
      </section>
    </Drawer>
  );
}

function Drawer({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-900/20" aria-hidden="true" onClick={onClose} />
      <div className="relative w-full max-w-xl h-full bg-white shadow-xl overflow-y-auto">{children}</div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="min-w-0"><div className="text-[11px] text-ink-tertiary">{label}</div><div className="truncate">{children}</div></div>;
}

function ActionForm({ action, shop, me, admins, onCancel, onSubmit }: {
  action: Exclude<Action, null>; shop: Shop; me: Me; admins: { id: string; name: string }[];
  onCancel: () => void; onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [v, setV] = useState<Record<string, string>>({ plan: shop.plan || "shop", status: "", admin_id: "" });
  const set = (k: string) => (e: { target: { value: string } }) => setV((p) => ({ ...p, [k]: e.target.value }));
  const lim = me.limits || {};
  function submit(e: FormEvent) {
    e.preventDefault();
    const reason = (v.reason || "").trim();
    if (action === "discount") onSubmit({ pct: Number(v.pct), until: v.until || null, reason });
    if (action === "trial") onSubmit({ days: Number(v.days), reason });
    if (action === "plan") onSubmit({ plan: v.plan, reason });
    if (action === "suspend") onSubmit({ reason, message: v.message || null });
    if (action === "unsuspend") onSubmit({ reason, status: v.status || null });
    if (action === "assign") onSubmit({ admin_id: v.admin_id || me.id, reason });
  }
  return (
    <form onSubmit={submit} className="mx-6 mt-4 p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-3 text-sm">
      {action === "discount" && (<>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs font-semibold">Percent off<input required type="number" min="1" max="100" step="0.5" value={v.pct || ""} onChange={set("pct")} className={`${input} mt-1`} /></label>
          <label className="text-xs font-semibold">Ends after<input type="date" value={v.until || ""} onChange={set("until")} className={`${input} mt-1`} /></label>
        </div>
        {lim.max_discount_pct !== undefined && <p className="text-xs text-ink-tertiary">Up to {lim.max_discount_pct}% for {lim.max_discount_months ?? "—"} months goes through at once. More is sent to the owner.</p>}
      </>)}
      {action === "trial" && (<>
        <label className="text-xs font-semibold block">Extra days<input required type="number" min="1" max="90" value={v.days || ""} onChange={set("days")} className={`${input} mt-1`} /></label>
        {lim.max_trial_days !== undefined && <p className="text-xs text-ink-tertiary">Up to {lim.max_trial_days} days goes through at once.</p>}
      </>)}
      {action === "plan" && (
        <label className="text-xs font-semibold block">New plan
          <select value={v.plan} onChange={set("plan")} className={`${input} mt-1`}>
            {["trial", "solo", "shop", "pro"].map((p) => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}
          </select></label>
      )}
      {action === "suspend" && (
        <label className="text-xs font-semibold block">Message the shop sees (optional)
          <input value={v.message || ""} onChange={set("message")} maxLength={500} placeholder="Payment overdue. Call Tekly on 080 1234 5678." className={`${input} mt-1`} /></label>
      )}
      {action === "unsuspend" && (
        <label className="text-xs font-semibold block">Return the shop to
          <select value={v.status} onChange={set("status")} className={`${input} mt-1`}><option value="">Where it was before</option><option value="active">Paying</option><option value="trial">Trial</option></select></label>
      )}
      {action === "assign" && (
        <label className="text-xs font-semibold block">Hand to
          <select value={v.admin_id} onChange={set("admin_id")} className={`${input} mt-1`}>
            <option value="">Me</option>{admins.filter((a) => a.id !== me.id).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select></label>
      )}
      <label className="text-xs font-semibold block">Reason<input required minLength={3} value={v.reason || ""} onChange={set("reason")} placeholder="Why, for the history" className={`${input} mt-1`} /></label>
      <div className="flex gap-2 justify-end">
        <button type="button" onClick={onCancel} className={btn}>Cancel</button>
        <button className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold">Save</button>
      </div>
    </form>
  );
}

function Notes({ shopId, notes, canAdd, onAdded }: { shopId: string; notes: NonNullable<Shop["notes"]>; canAdd: boolean; onAdded: () => void }) {
  const [body, setBody] = useState("");
  const [kind, setKind] = useState("note");
  const [err, setErr] = useState("");
  async function add(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) { setErr("Write something first"); return; }
    try { await adminApi.post(`/tenants/${shopId}/notes`, { body, kind }); setBody(""); setErr(""); onAdded(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <section className="px-6 py-4 border-t border-slate-100 mt-3">
      <h3 className="text-sm font-semibold mb-2">Notes</h3>
      {canAdd && (
        <form onSubmit={add} className="flex gap-2 mb-3">
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="px-2 py-1.5 rounded-lg border border-slate-300 text-sm" aria-label="Kind">
            <option value="note">Note</option><option value="call">Call</option><option value="email">Email</option></select>
          <input value={body} onChange={(e) => { setBody(e.target.value); setErr(""); }} placeholder="Called, wants a demo on Friday" className="flex-1 px-3 py-1.5 rounded-lg border border-slate-300 text-sm" aria-label="Note" />
          <button className={btn}>Add</button>
        </form>
      )}
      {err && <p role="alert" className="text-xs text-rose-600 mb-2">{err}</p>}
      {notes.length === 0 && <p className="text-sm text-ink-tertiary">No notes yet.</p>}
      {notes.map((n) => (
        <div key={n.id} className="text-sm py-1.5 border-b border-slate-50 last:border-0">
          <span className="text-[11px] uppercase tracking-wide text-ink-tertiary mr-2">{n.kind}</span>{n.body}
          <div className="text-[11px] text-ink-tertiary">{n.by} · {fmtDate(n.at)}</div>
        </div>
      ))}
    </section>
  );
}
