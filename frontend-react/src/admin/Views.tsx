import { useCallback, useEffect, useState, type FormEvent } from "react";
import { adminApi, fmtDate, type Me } from "./api";

const card = "bg-white rounded-xl border border-slate-200";
const input = "px-3 py-2 rounded-lg border border-slate-300 text-sm";
const btn = "px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold hover:bg-slate-50";
const primary = "px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold hover:opacity-90";

const FIELD_LABELS: Record<string, string> = {
  billing: "Discounts and what the shop pays", owner_contact: "Owner's email and phone", staff: "Staff list",
  notes: "Notes", usage: "Jobs and activity", revenue: "Company revenue",
};
const LEVEL_LABELS: Record<string, string> = { hidden: "Hidden", own: "Their shops only", shown: "Shown" };
const STAGE_LABELS: Record<string, string> = { trial: "Trials", paying: "Paying", lost: "Cancelled", suspended: "Suspended" };

type Admin = { id: string; name: string; email: string; active: boolean; role: string; role_id: string | null; two_step: boolean; last_login_at: string | null; shops: number };
type Role = { id: string; name: string; builtin: string | null; permissions: Record<string, boolean>; field_levels: Record<string, string>; scope: any; limits: Record<string, number> };
type Catalog = { permissions: Record<string, { code: string; label: string }[]>; field_groups: string[]; stages: string[] };

// ── Team: people and roles ───────────────────────────────────────────────────
export function TeamView({ me }: { me: Me }) {
  const [admins, setAdmins] = useState<Admin[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const [inviting, setInviting] = useState(false);
  const [leaving, setLeaving] = useState<Admin | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try {
      const [a, r] = await Promise.all([adminApi.get<{ items: Admin[] }>("/admins"), adminApi.get<{ items: Role[]; catalog: Catalog }>("/roles")]);
      setAdmins(a.items); setRoles(r.items); setCatalog(r.catalog);
    } catch (e: any) { setErr(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  async function changeRole(a: Admin, role: string) {
    const reason = window.prompt(`Why move ${a.name} to this role?`) || "";
    if (reason.trim().length < 3) return;
    try { await adminApi.patch(`/admins/${a.id}`, { role, reason }); load(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="space-y-6">
      <section className={card}>
        <div className="px-4 py-3 border-b border-slate-100 flex items-center">
          <h2 className="text-sm font-semibold">People</h2>
          <button onClick={() => setInviting(true)} className={`${primary} ml-auto`}>Add person</button>
        </div>
        {inviting && <InviteForm roles={roles} onDone={() => { setInviting(false); load(); }} onCancel={() => setInviting(false)} />}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-ink-tertiary border-b border-slate-100">
              <th className="px-4 py-2 font-semibold">Name</th><th className="px-4 py-2 font-semibold">Role</th>
              <th className="px-4 py-2 font-semibold">Shops</th><th className="px-4 py-2 font-semibold">2-step</th>
              <th className="px-4 py-2 font-semibold">Last sign-in</th><th /></tr></thead>
            <tbody>
              {admins.map((a) => (
                <tr key={a.id} className={`border-b border-slate-100 last:border-0 ${a.active ? "" : "opacity-50"}`}>
                  <td className="px-4 py-2.5"><div className="font-semibold">{a.name}</div><div className="text-xs text-ink-tertiary">{a.email}</div></td>
                  <td className="px-4 py-2.5">
                    {a.active && a.id !== me.id ? (
                      <select value={a.role_id || roles.find((r) => r.builtin === "owner")?.id || ""} onChange={(e) => changeRole(a, e.target.value)}
                        className="px-2 py-1 rounded-lg border border-slate-200 text-sm" aria-label={`Role for ${a.name}`}>
                        {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                      </select>) : a.role}
                  </td>
                  <td className="px-4 py-2.5 tabular-nums">{a.shops}</td>
                  <td className="px-4 py-2.5">{a.two_step ? "On" : "Not set up yet"}</td>
                  <td className="px-4 py-2.5 text-ink-secondary">{fmtDate(a.last_login_at)}</td>
                  <td className="px-4 py-2.5 text-right">{a.active && a.id !== me.id && <button onClick={() => setLeaving(a)} className={`${btn} text-rose-600`}>Remove</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {leaving && <LeaveForm person={leaving} others={admins.filter((x) => x.active && x.id !== leaving.id)} onDone={() => { setLeaving(null); load(); }} onCancel={() => setLeaving(null)} />}
      </section>

      <section className={card}>
        <div className="px-4 py-3 border-b border-slate-100 flex items-center">
          <h2 className="text-sm font-semibold">Roles</h2>
          {me.permissions.edit_roles && <button onClick={() => setEditing("new")} className={`${btn} ml-auto`}>New role</button>}
        </div>
        {roles.map((r) => (
          <div key={r.id} className="px-4 py-3 border-b border-slate-100 last:border-0 flex items-center gap-3 text-sm">
            <span className="font-semibold">{r.name}</span>
            <span className="text-ink-tertiary text-xs">{Object.values(r.permissions).filter(Boolean).length} permissions
              {r.limits?.max_discount_pct !== undefined ? ` · discounts up to ${r.limits.max_discount_pct}%` : ""}
              {r.scope?.own_book ? " · their shops only" : ""}</span>
            {me.permissions.edit_roles && r.builtin !== "owner" && <button onClick={() => setEditing(r)} className={`${btn} ml-auto`}>Edit</button>}
          </div>
        ))}
      </section>
      {editing && catalog && <RoleEditor role={editing === "new" ? null : editing} catalog={catalog} onDone={() => { setEditing(null); load(); }} onCancel={() => setEditing(null)} />}
      {err && <p role="alert" className="text-sm text-rose-600">{err}</p>}
    </div>
  );
}

function InviteForm({ roles, onDone, onCancel }: { roles: Role[]; onDone: () => void; onCancel: () => void }) {
  const [v, setV] = useState({ name: "", email: "", password: "", role: roles.find((r) => r.builtin === "sales_am")?.id || roles[0]?.id || "" });
  const [err, setErr] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    try { await adminApi.post("/admins", v); onDone(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <form onSubmit={submit} className="px-4 py-4 border-b border-slate-100 bg-slate-50 grid sm:grid-cols-2 gap-3">
      <input required placeholder="Name" value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} className={input} aria-label="Name" />
      <input required type="email" placeholder="name@tekly.app" value={v.email} onChange={(e) => setV({ ...v, email: e.target.value })} className={input} aria-label="Email" />
      <input required type="password" minLength={12} placeholder="Temporary password, 12+ characters" value={v.password} onChange={(e) => setV({ ...v, password: e.target.value })} className={input} aria-label="Temporary password" />
      <select value={v.role} onChange={(e) => setV({ ...v, role: e.target.value })} className={input} aria-label="Role">
        {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
      <p className="sm:col-span-2 text-xs text-ink-tertiary">They set up 2-step sign-in the first time they sign in.</p>
      {err && <p role="alert" className="sm:col-span-2 text-sm text-rose-600">{err}</p>}
      <div className="sm:col-span-2 flex justify-end gap-2"><button type="button" onClick={onCancel} className={btn}>Cancel</button><button className={primary}>Add</button></div>
    </form>
  );
}

function LeaveForm({ person, others, onDone, onCancel }: { person: Admin; others: Admin[]; onDone: () => void; onCancel: () => void }) {
  const [to, setTo] = useState(others[0]?.id || "");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    try { await adminApi.post(`/admins/${person.id}/deactivate`, { reassign_to: person.shops ? to : null, reason }); onDone(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <form onSubmit={submit} className="px-4 py-4 border-t border-slate-100 bg-rose-50/40 space-y-3 text-sm">
      <p>Remove <b>{person.name}</b>. They're signed out at once and their open requests are cancelled.</p>
      {person.shops > 0 && (
        <label className="block text-xs font-semibold">Who takes over their {person.shops} shops?
          <select value={to} onChange={(e) => setTo(e.target.value)} className={`${input} block mt-1`}>{others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
      )}
      <input required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason" className={`${input} w-full`} aria-label="Reason" />
      {err && <p role="alert" className="text-rose-600">{err}</p>}
      <div className="flex justify-end gap-2"><button type="button" onClick={onCancel} className={btn}>Cancel</button><button className="px-3 py-1.5 rounded-lg bg-rose-600 text-white text-xs font-semibold">Remove</button></div>
    </form>
  );
}

function RoleEditor({ role, catalog, onDone, onCancel }: { role: Role | null; catalog: Catalog; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState(role?.name || "");
  const [perms, setPerms] = useState<Record<string, boolean>>(role?.permissions || {});
  const [levels, setLevels] = useState<Record<string, string>>(role?.field_levels || {});
  const [stages, setStages] = useState<string[] | null>(role?.scope?.stages ?? null);
  const [countries, setCountries] = useState<string>((role?.scope?.countries || []).join(", "));
  const [ownBook, setOwnBook] = useState<boolean>(!!role?.scope?.own_book);
  const [limits, setLimits] = useState<Record<string, string>>(Object.fromEntries(Object.entries(role?.limits || {}).map(([k, v]) => [k, String(v)])));
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  async function save(e: FormEvent) {
    e.preventDefault();
    const body = {
      name, permissions: perms, field_levels: levels, reason,
      scope: { own_book: ownBook, countries: countries.split(",").map((c) => c.trim()).filter(Boolean), ...(stages ? { stages } : {}) },
      limits: Object.fromEntries(Object.entries(limits).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)])),
    };
    try { role ? await adminApi.patch(`/roles/${role.id}`, body) : await adminApi.post("/roles", body); onDone(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <form onSubmit={save} className={`${card} p-5 space-y-5 text-sm`}>
      <div className="flex items-center gap-3"><h2 className="font-semibold">{role ? `Edit ${role.name}` : "New role"}</h2>
        <input required value={name} onChange={(e) => setName(e.target.value)} className={`${input} ml-auto`} aria-label="Role name" /></div>
      <div className="grid md:grid-cols-2 gap-5">
        {Object.entries(catalog.permissions).map(([group, items]) => (
          <fieldset key={group}><legend className="text-xs font-semibold text-ink-tertiary mb-1">{group}</legend>
            {items.map((p) => (
              <label key={p.code} className="flex items-center gap-2 py-0.5">
                <input type="checkbox" checked={!!perms[p.code]} onChange={(e) => setPerms({ ...perms, [p.code]: e.target.checked })} />{p.label}</label>
            ))}
          </fieldset>
        ))}
      </div>
      <fieldset><legend className="text-xs font-semibold text-ink-tertiary mb-1">What they can see</legend>
        {catalog.field_groups.map((g) => (
          <div key={g} className="flex items-center gap-3 py-0.5"><span className="w-64">{FIELD_LABELS[g] || g}</span>
            <select value={levels[g] || "hidden"} onChange={(e) => setLevels({ ...levels, [g]: e.target.value })} className="px-2 py-1 rounded-lg border border-slate-200" aria-label={FIELD_LABELS[g] || g}>
              {Object.entries(LEVEL_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        ))}
      </fieldset>
      <fieldset className="space-y-2"><legend className="text-xs font-semibold text-ink-tertiary mb-1">Which shops</legend>
        <label className="flex items-center gap-2"><input type="checkbox" checked={ownBook} onChange={(e) => setOwnBook(e.target.checked)} />Only shops assigned to them</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={stages !== null} onChange={(e) => setStages(e.target.checked ? ["trial"] : null)} />Only some stages</label>
        {stages !== null && <div className="flex gap-3 pl-6">{catalog.stages.map((st) => (
          <label key={st} className="flex items-center gap-1"><input type="checkbox" checked={stages.includes(st)} onChange={(e) => setStages(e.target.checked ? [...stages, st] : stages.filter((x) => x !== st))} />{STAGE_LABELS[st] || st}</label>))}</div>}
        <input value={countries} onChange={(e) => setCountries(e.target.value)} placeholder="Countries, e.g. IN, AE (blank for all)" className={`${input} w-full`} aria-label="Countries" />
      </fieldset>
      <fieldset className="grid sm:grid-cols-3 gap-3"><legend className="text-xs font-semibold text-ink-tertiary mb-1 col-span-full">Limits (more needs the owner's OK)</legend>
        {[["max_discount_pct", "Discount up to %"], ["max_discount_months", "For up to months"], ["max_trial_days", "Extra trial days"]].map(([k, l]) => (
          <label key={k} className="text-xs font-semibold">{l}<input type="number" min="0" value={limits[k] ?? ""} onChange={(e) => setLimits({ ...limits, [k]: e.target.value })} className={`${input} w-full mt-1`} /></label>
        ))}
      </fieldset>
      <input required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this change" className={`${input} w-full`} aria-label="Reason" />
      {err && <p role="alert" className="text-rose-600">{err}</p>}
      <div className="flex justify-end gap-2"><button type="button" onClick={onCancel} className={btn}>Cancel</button><button className={primary}>Save role</button></div>
    </form>
  );
}

// ── Codes ────────────────────────────────────────────────────────────────────
export function CodesView() {
  const [items, setItems] = useState<any[]>([]);
  const [v, setV] = useState({ code: "", percent: "", expires_at: "", plan: "", note: "" });
  const [err, setErr] = useState("");
  const load = useCallback(() => adminApi.get<{ items: any[] }>("/coupons").then((r) => setItems(r.items)).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  async function create(e: FormEvent) {
    e.preventDefault(); setErr("");
    try {
      await adminApi.post("/coupons", { code: v.code || null, percent: Number(v.percent), expires_at: v.expires_at || null, plan: v.plan || null, note: v.note || null });
      setV({ code: "", percent: "", expires_at: "", plan: "", note: "" }); load();
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="space-y-4">
      <form onSubmit={create} className={`${card} p-4 grid sm:grid-cols-5 gap-3 text-sm`}>
        <input value={v.code} onChange={(e) => setV({ ...v, code: e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "") })} placeholder="LAUNCH30 (blank makes one)" className={input} aria-label="Code" />
        <input required type="number" min="1" max="100" value={v.percent} onChange={(e) => setV({ ...v, percent: e.target.value })} placeholder="Percent off" className={input} aria-label="Percent off" />
        <input type="date" value={v.expires_at} onChange={(e) => setV({ ...v, expires_at: e.target.value })} className={input} aria-label="Expires" />
        <select value={v.plan} onChange={(e) => setV({ ...v, plan: e.target.value })} className={input} aria-label="Plan"><option value="">Any plan</option>{["solo", "shop", "pro"].map((p) => <option key={p} value={p}>{p}</option>)}</select>
        <button className={primary}>Create code</button>
        <input value={v.note} onChange={(e) => setV({ ...v, note: e.target.value })} placeholder="Note, e.g. Partner: Parts Hub" className={`${input} sm:col-span-5`} aria-label="Note" />
      </form>
      {err && <p role="alert" className="text-sm text-rose-600">{err}</p>}
      <div className={card}>
        {items.length === 0 && <p className="px-4 py-4 text-sm text-ink-tertiary">No codes yet.</p>}
        {items.map((c) => (
          <div key={c.id} className="px-4 py-2.5 border-b border-slate-100 last:border-0 flex gap-4 text-sm">
            <span className="font-mono font-semibold">{c.code}</span><span>{c.percent}% off</span>
            <span className="text-ink-tertiary">{c.plan ? `${c.plan} plan` : "Any plan"} · {c.expires_at ? `until ${fmtDate(c.expires_at)}` : "no end date"}</span>
            {c.note && <span className="text-ink-secondary truncate">{c.note}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Action log ───────────────────────────────────────────────────────────────
export function LogView() {
  const [data, setData] = useState<{ items: any[]; chain_ok: boolean } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { adminApi.get<{ items: any[]; chain_ok: boolean }>("/log?limit=200").then(setData).catch((e) => setErr(e.message)); }, []);
  if (!data) return <p className="text-sm text-ink-tertiary">{err || "Loading…"}</p>;
  return (
    <div className="space-y-3">
      <p className={`text-sm ${data.chain_ok ? "text-emerald-700" : "text-rose-600 font-semibold"}`}>
        {data.chain_ok ? "The log is intact: no entry has been changed or removed." : "Warning: the log's chain is broken. An entry was changed or removed."}</p>
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-ink-tertiary border-b border-slate-100">
            <th className="px-4 py-2 font-semibold">When</th><th className="px-4 py-2 font-semibold">Who</th><th className="px-4 py-2 font-semibold">What</th>
            <th className="px-4 py-2 font-semibold">Result</th><th className="px-4 py-2 font-semibold">Reason</th></tr></thead>
          <tbody>
            {data.items.map((r) => (
              <tr key={r.id} className="border-b border-slate-100 last:border-0 align-top">
                <td className="px-4 py-2 whitespace-nowrap text-ink-secondary">{new Date(r.at + "Z").toLocaleString()}</td>
                <td className="px-4 py-2">{r.by || "Unknown"}</td>
                <td className="px-4 py-2">{r.action.replace(/_/g, " ")}{r.target ? <span className="text-ink-tertiary"> · {r.target}</span> : null}</td>
                <td className={`px-4 py-2 ${r.outcome === "refused" ? "text-rose-600" : r.outcome === "requested" ? "text-amber-700" : ""}`}>{r.outcome === "allowed" ? "Done" : r.outcome === "refused" ? "Refused" : "Sent for approval"}</td>
                <td className="px-4 py-2 text-ink-secondary">{r.reason || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
