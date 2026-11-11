import { useCallback, useEffect, useState, type FormEvent } from "react";
import { adminApi, actionLabel, fmtDate, fmtDateTime, type Me } from "./api";
import { AdminCard, rowCls } from "./ui";
import Button from "../components/Button";
import { PanelHeader } from "../components/PageLayout";
import { Field, Input, Select, labelClass } from "../components/Field";
import DateField from "../components/DateField";
import EmptyState, { TableEmpty } from "../components/EmptyState";
import SkeletonRows from "../components/SkeletonRows";
import { tableClass, theadRowClass, Th, Td } from "../components/Table";

const PLAN_LABEL: Record<string, string> = { solo: "Solo", shop: "Shop", pro: "Pro" };

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
  const [admins, setAdmins] = useState<Admin[] | null>(null);
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
  return (<>
    <PanelHeader title="Team" sub="The people who run Tekly, and what each role lets them do."
      actions={<>
        <Button variant="primary" onClick={() => setInviting(true)} disabled={inviting}>Add person</Button>
        <Button onClick={() => setEditing("new")} disabled={!me.permissions.edit_roles}>New role</Button>
      </>} />
    {inviting && <InviteForm roles={roles} onDone={() => { setInviting(false); load(); }} onCancel={() => setInviting(false)} />}
    {editing && catalog && <RoleEditor role={editing === "new" ? null : editing} catalog={catalog} onDone={() => { setEditing(null); load(); }} onCancel={() => setEditing(null)} />}
    <AdminCard title="People" meta={admins?.length}>
      <div className="overflow-x-auto">
        <table className={tableClass}>
          <thead><tr className={theadRowClass}>
            <Th>Name</Th><Th>Role</Th><Th num>Shops</Th><Th>2-step sign-in</Th><Th>Last sign-in</Th><Th><span className="sr-only">Actions</span></Th>
          </tr></thead>
          <tbody className="text-sm">
            {admins === null && <SkeletonRows cols={6} rows={3} />}
            {admins?.map((a) => (
              <tr key={a.id} className={`border-b border-slate-100 last:border-0 ${a.active ? "" : "opacity-50"}`}>
                <Td><div className="font-semibold">{a.name}</div><div className="text-xs text-ink-tertiary">{a.email}</div></Td>
                <Td>
                  {a.active && a.id !== me.id ? (
                    <Select value={a.role_id || roles.find((r) => r.builtin === "owner")?.id || ""} onChange={(e) => changeRole(a, e.target.value)}
                      className="min-w-[10rem]" aria-label={`Role for ${a.name}`}>
                      {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                    </Select>) : a.role}
                </Td>
                <Td num>{a.shops}</Td>
                <Td>{a.two_step ? "On" : "Not set up yet"}</Td>
                <Td className="text-ink-secondary">{fmtDate(a.last_login_at)}</Td>
                <Td className="text-right">{a.active && a.id !== me.id && <Button size="sm" variant="danger" onClick={() => setLeaving(a)}>Remove</Button>}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {leaving && <LeaveForm person={leaving} others={(admins || []).filter((x) => x.active && x.id !== leaving.id)} onDone={() => { setLeaving(null); load(); }} onCancel={() => setLeaving(null)} />}
    </AdminCard>

    <AdminCard title="Roles" meta={roles.length || undefined}>
      {admins === null ? <div className="px-4 py-4"><span className="sk block h-3 w-1/3 rounded" /></div> : roles.map((r) => (
        <div key={r.id} className={rowCls}>
          <span className="font-semibold">{r.name}</span>
          <span className="text-xs text-ink-tertiary">{Object.values(r.permissions).filter(Boolean).length} permissions
            {r.limits?.max_discount_pct !== undefined ? ` · discounts up to ${r.limits.max_discount_pct}%` : ""}
            {r.scope?.own_book ? " · their shops only" : ""}</span>
          {me.permissions.edit_roles && r.builtin !== "owner" && <Button size="sm" className="ml-auto" onClick={() => setEditing(r)}>Edit</Button>}
        </div>
      ))}
    </AdminCard>
    {err && <p role="alert" className="text-sm text-rose-600">{err}</p>}
  </>);
}

function InviteForm({ roles, onDone, onCancel }: { roles: Role[]; onDone: () => void; onCancel: () => void }) {
  const [v, setV] = useState({ name: "", email: "", password: "", role: roles.find((r) => r.builtin === "sales_am")?.id || roles[0]?.id || "" });
  const [err, setErr] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    try { await adminApi.post("/admins", v); onDone(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <AdminCard title="Add a person">
      <form onSubmit={submit} className="grid gap-3 p-4 sm:grid-cols-2">
        <Field label="Name" htmlFor="inv-name"><Input id="inv-name" required value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} aria-label="Name" /></Field>
        <Field label="Email" htmlFor="inv-email"><Input id="inv-email" required type="email" placeholder="name@tekly.app" value={v.email} onChange={(e) => setV({ ...v, email: e.target.value })} aria-label="Email" /></Field>
        <Field label="Temporary password" htmlFor="inv-pass" hint="12 or more characters."><Input id="inv-pass" required type="password" minLength={12} value={v.password} onChange={(e) => setV({ ...v, password: e.target.value })} aria-label="Temporary password" /></Field>
        <Field label="Role" htmlFor="inv-role"><Select id="inv-role" value={v.role} onChange={(e) => setV({ ...v, role: e.target.value })} aria-label="Role">
          {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</Select></Field>
        <p className="text-xs text-ink-tertiary sm:col-span-2">They set up 2-step sign-in the first time they sign in.</p>
        {err && <p role="alert" className="text-sm text-rose-600 sm:col-span-2">{err}</p>}
        <div className="flex gap-2 sm:col-span-2"><Button type="submit" variant="primary">Add</Button><Button onClick={onCancel}>Cancel</Button></div>
      </form>
    </AdminCard>
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
    <form onSubmit={submit} className="space-y-3 border-t border-slate-100 bg-rose-50 px-4 py-4 text-sm">
      <p>Remove <b>{person.name}</b>. They're signed out at once and their open requests are cancelled.</p>
      {person.shops > 0 && (
        <Field label={`Who takes over their ${person.shops} shops?`} htmlFor="leave-to">
          <Select id="leave-to" value={to} onChange={(e) => setTo(e.target.value)}>{others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</Select>
        </Field>
      )}
      <Field label="Reason" htmlFor="leave-reason"><Input id="leave-reason" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason" /></Field>
      {err && <p role="alert" className="text-rose-600">{err}</p>}
      <div className="flex gap-2"><Button type="submit" variant="dangerSolid">Remove</Button><Button onClick={onCancel}>Cancel</Button></div>
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
    <AdminCard title={role ? `Edit ${role.name}` : "New role"}>
      <form onSubmit={save} className="space-y-5 p-4 text-sm">
        <Field label="Role name" htmlFor="role-name" className="max-w-sm"><Input id="role-name" required value={name} onChange={(e) => setName(e.target.value)} aria-label="Role name" /></Field>
        <div className="grid gap-5 md:grid-cols-2">
          {Object.entries(catalog.permissions).map(([group, items]) => (
            <fieldset key={group}><legend className={labelClass}>{group}</legend>
              {items.map((p) => (
                <label key={p.code} className="flex items-center gap-2 py-0.5">
                  <input type="checkbox" checked={!!perms[p.code]} onChange={(e) => setPerms({ ...perms, [p.code]: e.target.checked })} />{p.label}</label>
              ))}
            </fieldset>
          ))}
        </div>
        <fieldset><legend className={labelClass}>What they can see</legend>
          {catalog.field_groups.map((g) => (
            <div key={g} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1"><span className="w-64">{FIELD_LABELS[g] || g}</span>
              <div className="w-44"><Select value={levels[g] || "hidden"} onChange={(e) => setLevels({ ...levels, [g]: e.target.value })} aria-label={FIELD_LABELS[g] || g}>
                {Object.entries(LEVEL_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></div></div>
          ))}
        </fieldset>
        <fieldset className="space-y-2"><legend className={labelClass}>Which shops</legend>
          <label className="flex items-center gap-2"><input type="checkbox" checked={ownBook} onChange={(e) => setOwnBook(e.target.checked)} />Only shops assigned to them</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={stages !== null} onChange={(e) => setStages(e.target.checked ? ["trial"] : null)} />Only some stages</label>
          {stages !== null && <div className="flex flex-wrap gap-3 pl-6">{catalog.stages.map((st) => (
            <label key={st} className="flex items-center gap-1"><input type="checkbox" checked={stages.includes(st)} onChange={(e) => setStages(e.target.checked ? [...stages, st] : stages.filter((x) => x !== st))} />{STAGE_LABELS[st] || st}</label>))}</div>}
          <Field label="Countries" htmlFor="role-countries" hint="For example IN, AE. Leave blank for all."><Input id="role-countries" value={countries} onChange={(e) => setCountries(e.target.value)} aria-label="Countries" /></Field>
        </fieldset>
        <fieldset className="grid gap-3 sm:grid-cols-3"><legend className={`${labelClass} col-span-full`}>Limits (more needs the owner's OK)</legend>
          {[["max_discount_pct", "Discount up to %"], ["max_discount_months", "For up to months"], ["max_trial_days", "Extra trial days"]].map(([k, l]) => (
            <Field key={k} label={l} htmlFor={`role-${k}`}><Input id={`role-${k}`} type="number" min="0" value={limits[k] ?? ""} onChange={(e) => setLimits({ ...limits, [k]: e.target.value })} /></Field>
          ))}
        </fieldset>
        <Field label="Why this change" htmlFor="role-reason"><Input id="role-reason" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason" /></Field>
        {err && <p role="alert" className="text-rose-600">{err}</p>}
        <div className="flex gap-2"><Button type="submit" variant="primary">Save role</Button><Button onClick={onCancel}>Cancel</Button></div>
      </form>
    </AdminCard>
  );
}

// ── Codes ────────────────────────────────────────────────────────────────────
export function CodesView() {
  const [items, setItems] = useState<any[] | null>(null);
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
  return (<>
    <PanelHeader title="Codes" sub="Discount codes a shop can enter when it subscribes." />
    <AdminCard title="New code">
      <form onSubmit={create} className="grid gap-3 p-4 sm:grid-cols-4">
        <Field label="Code" htmlFor="code-code" hint="Leave blank and we'll make one.">
          <Input id="code-code" value={v.code} onChange={(e) => setV({ ...v, code: e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "") })} placeholder="LAUNCH30" aria-label="Code" /></Field>
        <Field label="Percent off" htmlFor="code-pct">
          <Input id="code-pct" required type="number" min="1" max="100" value={v.percent} onChange={(e) => setV({ ...v, percent: e.target.value })} aria-label="Percent off" /></Field>
        <div><span className={labelClass}>Ends after</span><DateField value={v.expires_at} onChange={(x) => setV({ ...v, expires_at: x })} placeholder="No end date" label="Expires" /></div>
        <Field label="Plan" htmlFor="code-plan">
          <Select id="code-plan" value={v.plan} onChange={(e) => setV({ ...v, plan: e.target.value })} aria-label="Plan"><option value="">Any plan</option>{["solo", "shop", "pro"].map((p) => <option key={p} value={p}>{PLAN_LABEL[p]}</option>)}</Select></Field>
        <Field label="Note" htmlFor="code-note" className="sm:col-span-4">
          <Input id="code-note" value={v.note} onChange={(e) => setV({ ...v, note: e.target.value })} placeholder="e.g. Partner: Parts Hub" aria-label="Note" /></Field>
        {err && <p role="alert" className="text-sm text-rose-600 sm:col-span-4">{err}</p>}
        <div className="sm:col-span-4"><Button type="submit" variant="primary">Create code</Button></div>
      </form>
    </AdminCard>
    <AdminCard title="Codes" meta={items?.length}>
      <div className="overflow-x-auto">
        <table className={tableClass}>
          <thead><tr className={theadRowClass}><Th>Code</Th><Th num>Off</Th><Th>Plan</Th><Th>Ends</Th><Th>Note</Th></tr></thead>
          <tbody className="text-sm">
            {items === null && <SkeletonRows cols={5} rows={3} />}
            {items?.length === 0 && <TableEmpty cols={5} title="No codes yet" body="Codes you create show here." />}
            {items?.map((c) => (
              <tr key={c.id} className="border-b border-slate-100 last:border-0">
                <Td className="font-mono font-semibold">{c.code}</Td>
                <Td num>{c.percent}%</Td>
                <Td>{c.plan ? PLAN_LABEL[c.plan] || c.plan : "Any plan"}</Td>
                <Td className="whitespace-nowrap text-ink-secondary">{c.expires_at ? fmtDate(c.expires_at) : "No end date"}</Td>
                <Td className="text-ink-secondary">{c.note || ""}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AdminCard>
  </>);
}

// ── Action log ───────────────────────────────────────────────────────────────
const RESULT: Record<string, [string, string]> = { allowed: ["Done", ""], refused: ["Refused", "text-rose-600"], requested: ["Sent for approval", "text-amber-700"] };

export function LogView() {
  const [data, setData] = useState<{ items: any[]; chain_ok: boolean } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { adminApi.get<{ items: any[]; chain_ok: boolean }>("/log?limit=200").then(setData).catch((e) => setErr(e.message)); }, []);
  const result = (o: string) => RESULT[o] || RESULT.requested;
  return (<>
    <PanelHeader title="Action log" sub={!data ? (err || "Checking the log…")
      : <span className={data.chain_ok ? "text-emerald-700" : "font-semibold text-rose-600"}>{data.chain_ok ? "The log is intact: no entry has been changed or removed." : "Warning: the log's chain is broken. An entry was changed or removed."}</span>} />
    <AdminCard>
      {/* phone: one entry per row, nothing hidden off to the side */}
      <div className="sm:hidden">
        {!data && <div className="px-4 py-4"><span className="sk block h-3 w-1/2 rounded" /></div>}
        {data?.items.length === 0 && <EmptyState title="Nothing logged yet" />}
        {data?.items.map((r) => (
          <div key={r.id} className="border-b border-slate-100 px-4 py-3 text-sm last:border-0">
            <div className="flex gap-2"><span className="font-semibold">{actionLabel(r.action)}</span><span className={`ml-auto shrink-0 ${result(r.outcome)[1]}`}>{result(r.outcome)[0]}</span></div>
            <div className="mt-0.5 text-xs text-ink-tertiary">{r.by || "Unknown"} · {fmtDateTime(r.at)}{r.target ? ` · ${r.target}` : ""}</div>
            {r.reason && <div className="mt-0.5 text-xs text-ink-secondary">{r.reason}</div>}
          </div>
        ))}
      </div>
      <div className="hidden overflow-x-auto sm:block">
        <table className={tableClass}>
          <thead><tr className={theadRowClass}><Th>When</Th><Th>Who</Th><Th>What</Th><Th>Result</Th><Th>Reason</Th></tr></thead>
          <tbody className="text-sm">
            {!data && <SkeletonRows cols={5} rows={5} />}
            {data?.items.length === 0 && <TableEmpty cols={5} title="Nothing logged yet" />}
            {data?.items.map((r) => (
              <tr key={r.id} className="border-b border-slate-100 align-top last:border-0">
                <Td className="whitespace-nowrap text-ink-secondary">{fmtDateTime(r.at)}</Td>
                <Td>{r.by || "Unknown"}</Td>
                <Td>{actionLabel(r.action)}{r.target ? <span className="text-ink-tertiary"> · {r.target}</span> : null}</Td>
                <Td className={result(r.outcome)[1]}>{result(r.outcome)[0]}</Td>
                <Td className="text-ink-secondary">{r.reason || ""}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AdminCard>
  </>);
}
