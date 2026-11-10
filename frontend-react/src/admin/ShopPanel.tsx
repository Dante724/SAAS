import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { adminApi, actionLabel, fmtDate, SEGMENTS, type Me, type Shop } from "./api";
import { loadFailed } from "../lib/notice";
import Drawer from "../components/Drawer";
import Button from "../components/Button";
import { Field, Input, Select, labelClass } from "../components/Field";
import DateField from "../components/DateField";
import EmptyState from "../components/EmptyState";

type Action = "discount" | "trial" | "plan" | "billing" | "suspend" | "unsuspend" | "assign" | null;
const STATUSES: [string, string][] = [["trial", "Trial"], ["active", "Paying"], ["past_due", "Payment failed (grace)"],
  ["unpaid", "Unpaid (read-only)"], ["trial_lost", "Trial ended"], ["churned", "Cancelled"]];
const PLAN_LABEL: Record<string, string> = { solo: "Solo", shop: "Shop", pro: "Pro" };
const NOTE_KIND: Record<string, string> = { note: "Note", call: "Call", email: "Email" };

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

  if (!s) return (
    <Drawer title="Shop" label="Shop" onClose={onClose}>
      {err ? <p role="alert" className="text-sm text-rose-600">{err}</p> : <div className="space-y-2.5" role="status" aria-label="Loading"><span className="sk block h-3 w-1/2 rounded" /><span className="sk block h-3 w-1/3 rounded" /></div>}
    </Drawer>
  );
  const has = (k: keyof Shop) => s[k] !== undefined;
  return (
    <Drawer title={s.name} label={s.name} onClose={onClose}
      sub={<>{SEGMENTS[s.segment] || s.segment} · {PLAN_LABEL[s.plan] || s.plan} plan · {s.country || "No country"}</>}>
      <button onClick={() => navigator.clipboard?.writeText(s.account_id).then(() => setFlash("Account ID copied"))}
        className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-sm" title="Copy">{s.account_id}</button>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-slate-100 pb-4 text-sm">
        <Fact label="Trial ends">{fmtDate(s.trial_ends_at)}</Fact>
        <Fact label="Hard stop (blocks sign-in)">{fmtDate(s.sub_end)}</Fact>
        <Fact label="Billing">{s.billing_mode === "custom"
          ? `Custom price, managed by Tekly${s.billing_period_end ? ` · term ends ${fmtDate(s.billing_period_end)}` : ""}`
          : `Stripe, ${s.billing_period === "year" ? "yearly" : "monthly"}`}</Fact>
        {has("billing_note") && s.billing_note && <Fact label="Agreed price (internal)">{s.billing_note}</Fact>}
        {has("price_locked_until") && s.price_locked_until && <Fact label="Founding price locked until">{fmtDate(s.price_locked_until)}</Fact>}
        {has("stripe_customer_id") && s.stripe_customer_id && <Fact label="Stripe customer">{s.stripe_customer_id}</Fact>}
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

      <div className="flex flex-wrap gap-2">
        {can("give_discount") && <Button size="sm" onClick={() => setAction("discount")}>Discount</Button>}
        {can("extend_trial") && <Button size="sm" onClick={() => setAction("trial")}>Extend trial</Button>}
        {(can("change_plan") || can("request_plan_change")) && <Button size="sm" onClick={() => setAction("plan")}>Change plan</Button>}
        {can("change_plan") && <Button size="sm" onClick={() => setAction("billing")}>Billing terms</Button>}
        {can("assign_shops") && <Button size="sm" onClick={() => setAction("assign")}>Hand over</Button>}
        {can("suspend") && (s.sub_status === "suspended"
          ? <Button size="sm" onClick={() => setAction("unsuspend")}>Unsuspend</Button>
          : <Button size="sm" variant="danger" onClick={() => setAction("suspend")}>Suspend</Button>)}
      </div>

      {action && (
        <ActionForm action={action} shop={s} me={me} admins={admins} onCancel={() => setAction(null)}
          onSubmit={(body) => {
            if (action === "billing") { done(adminApi.patch(`/tenants/${id}`, body), "Billing terms saved"); return; }
            const map: Record<string, [string, string]> = {
              discount: [`/tenants/${id}/discount`, "Discount saved"], trial: [`/tenants/${id}/extend-trial`, "Trial extended"],
              plan: [`/tenants/${id}/plan`, "Plan changed"], suspend: [`/tenants/${id}/suspend`, "Shop suspended"],
              unsuspend: [`/tenants/${id}/unsuspend`, "Shop unsuspended"], assign: [`/tenants/${id}/assign`, "Shop handed over"],
            };
            const [url, ok] = map[action];
            done(adminApi.post(url, body), ok);
          }} />
      )}

      {(flash || err) && <p role={err ? "alert" : "status"} className={`text-sm ${err ? "text-rose-600" : "text-emerald-700"}`}>{err || flash}</p>}

      {has("notes") && <Notes shopId={id} notes={s.notes || []} canAdd={can("add_notes")} onAdded={load} />}

      {s.employees_detail && (
        <section className="border-t border-slate-100 pt-4">
          <h3 className="mb-2 text-sm font-semibold">Staff</h3>
          {s.employees_detail.map((e) => (
            <div key={e.id} className="text-sm flex gap-2 py-1"><span>{e.name}</span><span className="text-ink-tertiary">{e.email}</span>{!e.active && <span className="text-rose-600 text-xs">Inactive</span>}</div>
          ))}
        </section>
      )}

      <section className="border-t border-slate-100 pt-4">
        <h3 className="mb-2 text-sm font-semibold">History</h3>
        {(s.history || []).length === 0 && <EmptyState size="inline" title="No admin changes yet." />}
        {(s.history || []).map((h, i) => (
          <div key={i} className="border-b border-slate-100 py-1.5 text-xs last:border-0">
            <span className="font-semibold">{actionLabel(h.action)}</span> <span className="text-ink-tertiary">by {h.by} · {fmtDate(h.at)}</span>
            {h.reason && <div className="text-ink-secondary">{h.reason}</div>}
          </div>
        ))}
      </section>
    </Drawer>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="min-w-0"><div className="text-2xs text-ink-tertiary">{label}</div><div className="truncate">{children}</div></div>;
}

function ActionForm({ action, shop, me, admins, onCancel, onSubmit }: {
  action: Exclude<Action, null>; shop: Shop; me: Me; admins: { id: string; name: string }[];
  onCancel: () => void; onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [v, setV] = useState<Record<string, string>>({
    plan: shop.plan || "shop", status: "", admin_id: "",
    sub_status: shop.sub_status && shop.sub_status !== "suspended" ? shop.sub_status : "trial",
    trial_ends_at: shop.trial_ends_at || "", billing_mode: shop.billing_mode || "stripe",
    billing_period_end: shop.billing_period_end || "", billing_note: shop.billing_note || "",
    price_locked_until: shop.price_locked_until || "",
  });
  const set = (k: string) => (e: { target: { value: string } }) => setV((p) => ({ ...p, [k]: e.target.value }));
  const lim = me.limits || {};
  function submit(e: FormEvent) {
    e.preventDefault();
    const reason = (v.reason || "").trim();
    if (action === "discount") onSubmit({ pct: Number(v.pct), until: v.until || null, reason });
    if (action === "trial") onSubmit({ days: Number(v.days), reason });
    if (action === "plan") onSubmit({ plan: v.plan, reason });
    if (action === "billing") {
      const body: Record<string, unknown> = {
        trial_ends_at: v.trial_ends_at || null, billing_mode: v.billing_mode,
        billing_period_end: v.billing_period_end || null, price_locked_until: v.price_locked_until || null, reason,
      };
      if (shop.sub_status !== "suspended") body.sub_status = v.sub_status;   // suspending has its own action
      if (shop.billing_note !== undefined) body.billing_note = v.billing_note || null;   // only if this role sees it
      onSubmit(body);
    }
    if (action === "suspend") onSubmit({ reason, message: v.message || null });
    if (action === "unsuspend") onSubmit({ reason, status: v.status || null });
    if (action === "assign") onSubmit({ admin_id: v.admin_id || me.id, reason });
  }
  const dateField = (k: string, label: string) => (
    <div><span className={labelClass}>{label}</span><DateField value={v[k] || ""} onChange={(x) => setV((p) => ({ ...p, [k]: x }))} placeholder="No date" label={label} /></div>
  );
  return (
    <form onSubmit={submit} className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm">
      {action === "discount" && (<>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Percent off" htmlFor="act-pct"><Input id="act-pct" required type="number" min="1" max="100" step="0.5" value={v.pct || ""} onChange={set("pct")} /></Field>
          {dateField("until", "Ends after")}
        </div>
        {lim.max_discount_pct !== undefined && <p className="text-xs text-ink-tertiary">Up to {lim.max_discount_pct}% for {lim.max_discount_months ?? "—"} months goes through at once. More is sent to the owner.</p>}
      </>)}
      {action === "trial" && (<>
        <Field label="Extra days" htmlFor="act-days"><Input id="act-days" required type="number" min="1" max="90" value={v.days || ""} onChange={set("days")} /></Field>
        {lim.max_trial_days !== undefined && <p className="text-xs text-ink-tertiary">Up to {lim.max_trial_days} days goes through at once.</p>}
      </>)}
      {action === "plan" && (
        <Field label="New plan" htmlFor="act-plan">
          <Select id="act-plan" value={v.plan} onChange={set("plan")}>
            {["solo", "shop", "pro"].map((p) => <option key={p} value={p}>{PLAN_LABEL[p]}</option>)}
          </Select></Field>
      )}
      {action === "billing" && (<>
        <div className="grid grid-cols-2 gap-3">
          {shop.sub_status !== "suspended" && (
            <Field label="Status" htmlFor="act-status">
              <Select id="act-status" value={v.sub_status} onChange={set("sub_status")}>
                {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </Select></Field>
          )}
          {dateField("trial_ends_at", "Trial ends")}
          <Field label="Billing mode" htmlFor="act-mode">
            <Select id="act-mode" value={v.billing_mode} onChange={set("billing_mode")}>
              <option value="stripe">Stripe (self-serve)</option><option value="custom">Custom price (managed by Tekly)</option>
            </Select></Field>
          {dateField("billing_period_end", "Custom term ends")}
          {dateField("price_locked_until", "Founding price locked until")}
        </div>
        {shop.billing_note !== undefined && (
          <Field label="Agreed price (custom shops, internal)" htmlFor="act-note">
            <Input id="act-note" value={v.billing_note} onChange={set("billing_note")} maxLength={500} placeholder="e.g. ₹2,500 a month, invoiced quarterly" /></Field>
        )}
        <p className="text-xs text-ink-tertiary">Custom shops never see Stripe Checkout: set Status to Paying, and past the term end the shop becomes read-only.</p>
      </>)}
      {action === "suspend" && (
        <Field label="Message the shop sees (optional)" htmlFor="act-msg">
          <Input id="act-msg" value={v.message || ""} onChange={set("message")} maxLength={500} placeholder="Payment overdue. Call Tekly on 080 1234 5678." /></Field>
      )}
      {action === "unsuspend" && (
        <Field label="Return the shop to" htmlFor="act-back">
          <Select id="act-back" value={v.status} onChange={set("status")}><option value="">Where it was before</option><option value="active">Paying</option><option value="trial">Trial</option></Select></Field>
      )}
      {action === "assign" && (
        <Field label="Hand to" htmlFor="act-to">
          <Select id="act-to" value={v.admin_id} onChange={set("admin_id")}>
            <option value="">Me</option>{admins.filter((a) => a.id !== me.id).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select></Field>
      )}
      <Field label="Reason" htmlFor="act-reason"><Input id="act-reason" required minLength={3} value={v.reason || ""} onChange={set("reason")} placeholder="Why, for the history" /></Field>
      <div className="flex gap-2">
        <Button type="submit" variant="primary">Save</Button>
        <Button onClick={onCancel}>Cancel</Button>
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
    <section className="border-t border-slate-100 pt-4">
      <h3 className="mb-2 text-sm font-semibold">Notes</h3>
      {canAdd && (
        <form onSubmit={add} className="flex gap-2 mb-3">
          <div className="w-28 shrink-0"><Select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
            <option value="note">Note</option><option value="call">Call</option><option value="email">Email</option></Select></div>
          <Input value={body} onChange={(e) => { setBody(e.target.value); setErr(""); }} placeholder="Called, wants a demo on Friday" aria-label="Note" />
          <Button type="submit">Add</Button>
        </form>
      )}
      {err && <p role="alert" className="text-xs text-rose-600 mb-2">{err}</p>}
      {notes.length === 0 && <EmptyState size="inline" title="No notes yet." />}
      {notes.map((n) => (
        <div key={n.id} className="border-b border-slate-100 py-1.5 text-sm last:border-0">
          <span className="mr-2 text-2xs font-semibold text-ink-tertiary">{NOTE_KIND[n.kind] || n.kind}</span>{n.body}
          <div className="text-2xs text-ink-tertiary">{n.by} · {fmtDate(n.at)}</div>
        </div>
      ))}
    </section>
  );
}
