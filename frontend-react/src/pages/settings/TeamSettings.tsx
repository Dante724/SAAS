import { useEffect, useMemo, useState } from "react";
import { hueText, tint } from "../../lib/tint";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";

/* Settings › Team & roles — people (with pending invites), roles (who has each one and what it
   can see), and commission rules. Same endpoints as before; the drawers for people and
   commission rules live in the hub, the role editor lives here. */

const LANDING_PAGES: [string, string][] = [
  ["", "Home (default)"], ["/my-work", "My work"], ["/orders", "Work orders"],
  ["/inquiries", "Inquiries"], ["/appointments", "Appointments"], ["/invoices", "Invoices"],
  ["/inventory", "Inventory"], ["/reports", "Reports"],
];
// Home-feed signal keys (must match backend reports.home_feed item keys).
const FEED_SIGNALS: [string, string][] = [
  ["overdue", "Overdue work orders"], ["messages", "Customer messages waiting"],
  ["ready", "Ready for pickup"], ["urgent", "Urgent orders"],
  ["unpaid", "Unpaid invoices"], ["lowstock", "Low / out of stock"],
  ["tasks", "Open tasks"], ["inquiries", "Inquiries awaiting reply"],
];
const PRESETS: [string, string, string][] = [
  ["Full access", "Everything, like the owner", "verified_user"],
  ["Manager", "Runs the shop, no billing setup", "badge"],
  ["Specialist", "Works on jobs, sees their own pay", "build"],
  ["Partner", "Outside partner, limited view", "group"],
];

const HUES = ["#378ADD", "#7F77DD", "#D4537E", "#EF9F27", "#1D9E75", "#639922", "#D85A30", "#185FA5"];
const hue = (s: string) => HUES[[...(s || "?")].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length];
const initials = (n: string) => (n || "?").trim().split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() || "").join("") || "?";

export function Avatar({ name, size = 36 }: { name: string; size?: number }) {
  const c = hue(name);
  return <span className="rounded-full grid place-items-center font-semibold flex-shrink-0" style={{ width: size, height: size, background: tint(c), color: hueText(c), fontSize: size * 0.36 }}>{initials(name)}</span>;
}

type Tab = "people" | "roles" | "pay" | "signin";

export function TeamPage({ team, roles, invites, rules, catalog, payLabel, onEditPerson, onNewPerson, onInvite, onEditRole, onNewRole, onEditRule, onNewRule, onChanged, onFlash }: {
  team: any[] | null | undefined; roles: any[] | null; invites: any[] | null; rules: any[] | null | undefined; catalog: Record<string, any[]> | null;
  payLabel: (r: any) => { event: string; amount: string };
  onEditPerson: (e: any) => void; onNewPerson: () => void; onInvite: () => void;
  onEditRole: (r: any) => void; onNewRole: () => void; onEditRule: (r: any) => void; onNewRule: () => void;
  onChanged: () => void; onFlash: (m: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("people");
  const [q, setQ] = useState("");
  const [showBlocked, setShowBlocked] = useState(false);
  const people = team || [];
  const roleName = (id?: string) => roles?.find((r) => r.id === id)?.name;
  const liveCodes = catalog ? Object.values(catalog).flat().map((c: any) => c.code as string) : [];
  const totalPerms = liveCodes.length;
  const blocked = people.filter((p) => p.active === false).length;
  const ql = q.trim().toLowerCase();
  const shown = people.filter((p) => (showBlocked || p.active !== false)
    && (!ql || [p.name, p.email, p.phone, p.position, roleName(p.role_id)].some((v) => (v || "").toLowerCase().includes(ql))));

  async function cancelInvite(i: any) {
    if (!(await confirmDialog(`Cancel the invite for ${i.email}? The link stops working.`, { title: "Cancel invite", confirmLabel: "Cancel invite", danger: true }))) return;
    try { await api.del(`/staff/invites/${i.id}`); onFlash("Invite cancelled"); onChanged(); } catch (e: any) { onFlash(e?.message || "Couldn't cancel"); }
  }

  const tabs: [Tab, string, number | undefined][] = [["people", "People", people.length], ["roles", "Roles", roles?.length], ["pay", "Commissions", rules?.length], ["signin", "Sign-in", undefined]];
  const primary = tab === "people" ? { label: "Invite someone", icon: "person_add", on: onInvite }
    : tab === "roles" ? { label: "New role", icon: "add", on: onNewRole } : { label: "New rule", icon: "add", on: onNewRule };

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-start gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink-primary">Your team</h2>
          <p className="text-xs text-ink-tertiary">Who works here, what they can do, and how commission is paid.</p>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-shrink-0">
          {tab === "people" && <button onClick={onNewPerson} className="px-3.5 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Add without invite</button>}
          {tab !== "signin" && <button onClick={primary.on} className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-ink-primary text-white text-xs font-semibold hover:opacity-90"><M n={primary.icon} c="text-sm" />{primary.label}</button>}
        </div>
      </div>

      <div className="inline-flex rounded-xl bg-slate-100 p-1">
        {tabs.map(([id, label, n]) => (
          <button key={id} onClick={() => setTab(id)} aria-pressed={tab === id}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${tab === id ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>
            {label}{n != null && <span className="ml-1.5 text-ink-tertiary font-normal">{n}</span>}
          </button>
        ))}
      </div>

      {tab === "people" && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-2">
          <div className="flex items-center gap-2 px-2 pt-1 pb-2">
            <div className="relative flex-1">
              <M n="search" c="absolute left-3 top-2.5 text-base text-ink-tertiary pointer-events-none" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, email, role…" aria-label="Search people"
                className="w-full bg-slate-50 border border-transparent focus:border-slate-200 focus:bg-white rounded-xl pl-9 pr-3 py-2 text-sm outline-none" />
            </div>
            {blocked > 0 && (
              <button onClick={() => setShowBlocked((v) => !v)} aria-pressed={showBlocked} className={`px-3 py-2 rounded-xl text-xs font-semibold ${showBlocked ? "bg-slate-100 text-ink-primary" : "text-ink-tertiary hover:text-ink-secondary"}`}>{showBlocked ? "Hide" : "Show"} blocked ({blocked})</button>
            )}
          </div>

          {(invites || []).length > 0 && !ql && (
            <div className="mb-1">
              {(invites || []).map((i) => (
                <div key={i.id} className="group flex items-center gap-3 px-3 py-2.5 rounded-xl hover:bg-slate-50">
                  <span className="w-9 h-9 rounded-full border-2 border-dashed border-slate-300 grid place-items-center text-ink-tertiary flex-shrink-0"><M n="mail" c="text-sm" /></span>
                  <span className="min-w-0">
                    <span className="block text-sm text-ink-primary truncate">{i.name || i.email}</span>
                    <span className="block text-[11px] text-ink-tertiary truncate">{i.name ? `${i.email} · ` : ""}{roleName(i.role_id) || "No role yet"}</span>
                  </span>
                  <span className={`ml-auto text-[11px] font-semibold px-2 py-0.5 rounded-md ${i.expired ? "bg-rose-50 text-rose-600" : "bg-amber-50 text-amber-700"}`}>{i.expired ? "Invite expired" : "Invited"}</span>
                  <button onClick={() => cancelInvite(i)} aria-label={`Cancel invite for ${i.email}`} className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-rose-50 hover:text-rose-600 opacity-0 group-hover:opacity-100 focus:opacity-100"><M n="close" c="text-base" /></button>
                </div>
              ))}
              <div className="mx-3 my-1 border-t border-slate-100" />
            </div>
          )}

          {team == null && <div className="p-4 text-xs text-ink-tertiary">Loading your team…</div>}
          {team && shown.length === 0 && <div className="p-6 text-center text-xs text-ink-tertiary">{ql ? `No one matches "${q}".` : "No one here yet. Invite your first teammate."}</div>}
          {shown.map((p) => (
            <div key={p.id} onClick={() => onEditPerson(p)} className={`flex items-center gap-3 px-3 py-2.5 rounded-xl cursor-pointer hover:bg-slate-50 ${p.active === false ? "opacity-60" : ""}`}>
              <Avatar name={p.name} />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-ink-primary truncate">{p.name}</span>
                <span className="block text-[11px] text-ink-tertiary truncate">{[p.position, p.email].filter(Boolean).join(" · ")}</span>
              </span>
              <span className="ml-auto flex items-center gap-2 flex-shrink-0">
                {roleName(p.role_id)
                  ? <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 text-ink-secondary">{roleName(p.role_id)}</span>
                  : <span className="text-[11px] px-2 py-0.5 rounded-md border border-dashed border-slate-300 text-ink-tertiary">No role</span>}
                {p.active === false && <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-rose-50 text-rose-600">Blocked</span>}
                <M n="chevron_right" c="text-base text-ink-tertiary" />
              </span>
            </div>
          ))}
        </div>
      )}

      {tab === "roles" && (
        <div className="grid sm:grid-cols-2 gap-3">
          {(roles || []).map((r) => {
            const members = people.filter((p) => p.role_id === r.id && p.active !== false);
            const denied = liveCodes.filter((c) => (r.permissions || {})[c] === false).length;
            const allowedN = totalPerms ? totalPerms - denied : null;
            const limits = [r.order_scope === "own" && "Only their own jobs", r.contact_scope === "own" && "Only their own customers",
              r.location_scope && r.location_scope !== "all" && "Some locations", (r.hidden_status_keys || []).length > 0 && "Some statuses hidden"].filter(Boolean) as string[];
            return (
              <button key={r.id} onClick={() => onEditRole(r)} className="text-left bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 hover:border-slate-300 transition-colors">
                <div className="flex items-center gap-2">
                  <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center"><M n="badge" c="text-lg" /></span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-ink-primary truncate">{r.name}</span>
                    <span className="block text-[11px] text-ink-tertiary">{allowedN == null ? "" : denied === 0 ? "Can do everything" : `Can do ${allowedN} of ${totalPerms} things`}</span>
                  </span>
                </div>
                {totalPerms > 0 && <div className="mt-3 h-1.5 rounded-full bg-slate-100 overflow-hidden"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.round(((allowedN ?? 0) / totalPerms) * 100)}%` }} /></div>}
                <div className="flex flex-wrap gap-1 mt-3 min-h-[22px]">
                  {limits.length ? limits.map((l) => <span key={l} className="text-[11px] px-2 py-0.5 rounded-md bg-amber-50 text-amber-700">{l}</span>)
                    : <span className="text-[11px] text-ink-tertiary">Sees all records</span>}
                </div>
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-100">
                  <span className="flex -space-x-1.5">{members.slice(0, 4).map((m) => <span key={m.id} className="ring-2 ring-white rounded-full"><Avatar name={m.name} size={24} /></span>)}</span>
                  <span className="text-[11px] text-ink-tertiary">{members.length ? `${members.length} ${members.length === 1 ? "person" : "people"}` : "No one has this role"}</span>
                </div>
              </button>
            );
          })}
          <button onClick={onNewRole} className="rounded-2xl border border-dashed border-slate-300 p-4 text-xs font-semibold text-ink-secondary hover:bg-white hover:border-slate-400 grid place-items-center min-h-[150px]">
            <span className="flex flex-col items-center gap-1.5"><M n="add_circle" c="text-xl" />New role</span>
          </button>
        </div>
      )}

      {tab === "signin" && <DeviceLimitCard onFlash={onFlash} />}

      {tab === "pay" && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-2">
          {(rules || []).length === 0 && (
            <div className="p-6 text-center">
              <div className="text-sm text-ink-primary">No commission rules yet</div>
              <div className="text-xs text-ink-tertiary mt-0.5 mb-3">Pay a percent or a flat amount when work is done or a part is sold.</div>
              <button onClick={onNewRule} className="px-3.5 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-ink-primary hover:bg-slate-50">Create a rule</button>
            </div>
          )}
          {(rules || []).map((r) => {
            const who = people.find((p) => p.id === r.employee_id);
            const l = payLabel(r);
            return (
              <div key={r.id} onClick={() => onEditRule(r)} className="flex items-center gap-3 px-3 py-3 rounded-xl cursor-pointer hover:bg-slate-50">
                <span className="w-9 h-9 rounded-xl bg-emerald-50 text-emerald-700 grid place-items-center flex-shrink-0"><M n="payments" c="text-lg" /></span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-ink-primary truncate">{r.name}</span>
                  <span className="block text-[11px] text-ink-tertiary truncate">{l.event} · {who ? who.name : "Everyone it applies to"}</span>
                </span>
                <span className="ml-auto text-sm font-semibold text-ink-primary whitespace-nowrap">{l.amount}</span>
                <M n="chevron_right" c="text-base text-ink-tertiary" />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function RoleEditor({ role, catalog, statuses, members = 0, onClose, onSaved }: {
  role: any | null; catalog: Record<string, { code: string; label: string }[]>; statuses: any[]; members?: number; onClose: () => void; onSaved: () => void;
}) {
  const isNew = !role?.id;
  const [name, setName] = useState<string>(role?.name || "");
  const [preset, setPreset] = useState<string | null>(null);
  // Keep only real permission codes with yes/no values. Roles saved in the old format
  // ({"orders": "write", "finance": "none"}) carry keys the API rejects and that grant or deny nothing.
  const [perms, setPerms] = useState<Record<string, boolean>>(() => {
    const known = new Set(Object.values(catalog).flat().map((c) => c.code));
    return Object.fromEntries(Object.entries(role?.permissions || {}).filter(([k, v]) => known.has(k) && typeof v === "boolean")) as Record<string, boolean>;
  });
  const [contactScope, setContactScope] = useState<string>(role?.contact_scope || "all");
  const [orderScope, setOrderScope] = useState<string>(role?.order_scope || "all");
  const [locScope, setLocScope] = useState<string>(role?.location_scope || "all");
  const [locIds, setLocIds] = useState<string[]>(role?.location_ids || []);
  const [payrollDays, setPayrollDays] = useState<string>(role?.payroll_visibility_days != null ? String(role.payroll_visibility_days) : "");
  const [hiddenStatuses, setHiddenStatuses] = useState<string[]>(role?.hidden_status_keys || []);
  const [feedSources, setFeedSources] = useState<string[]>(role?.home_feed_sources || []);
  const [homeRoute, setHomeRoute] = useState<string>(role?.home_route || "");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [tab, setTab] = useState<"can" | "sees" | "home">("can");
  const [q, setQ] = useState("");
  const [locations, setLocations] = useState<any[]>([]);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<any>("/staff/locations").then((r) => setLocations(r.items || r || [])).catch(() => setLocations([])); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Saved roles are allow-lists on the server: a code they don't list is off, so show it off
  // (otherwise a permission added later looks granted here while the API refuses it).
  const explicit = !!role?.perms_explicit;
  const allowed = (code: string) => (explicit ? perms[code] === true : perms[code] !== false);
  const toggle = (code: string) => setPerms((p) => ({ ...p, [code]: !(explicit ? p[code] === true : p[code] !== false) }));
  const grantAll = (codes: { code: string }[], on: boolean) => setPerms((p) => { const n = { ...p }; codes.forEach((c) => { n[c.code] = on; }); return n; });
  const ql = q.trim().toLowerCase();
  const filtered = useMemo(() => Object.entries(catalog)
    .map(([g, codes]) => [g, ql ? codes.filter((c) => c.label.toLowerCase().includes(ql) || g.toLowerCase().includes(ql)) : codes] as [string, { code: string; label: string }[]])
    .filter(([, codes]) => codes.length > 0), [catalog, ql]);
  const all = Object.values(catalog).flat();
  const totalOn = all.filter((c) => allowed(c.code)).length;

  async function save() {
    if (!name.trim()) { setErr("Give the role a name"); return; }
    if (locScope !== "all" && locIds.length === 0) { setErr("Pick at least one location, or allow all locations"); return; }
    setBusy(true); setErr(null);
    const body: any = {
      name: name.trim(), permissions: perms,
      contact_scope: contactScope, order_scope: orderScope,
      // Sent back as they are so saving never widens a role's reach (the API resets missing ones to "all").
      location_scope: locScope, location_ids: locScope === "all" ? [] : locIds,
      warehouse_scope: role?.warehouse_scope || "all", warehouse_ids: role?.warehouse_ids || [],
      account_ids: role?.account_ids || [],
      payroll_visibility_days: payrollDays === "" ? null : Number(payrollDays),
      hidden_status_keys: hiddenStatuses, home_feed_sources: feedSources, home_route: homeRoute,
    };
    if (isNew && preset) body.template = preset;
    try {
      if (isNew) await api.post("/settings/rbac/roles", body); else await api.put(`/settings/rbac/roles/${role.id}`, body);
      onSaved();
    } catch (e: any) { setErr(e?.message || "Couldn't save the role"); setBusy(false); }
  }

  async function remove() {
    if (!(await confirmDialog(`Delete the role "${role.name}"?`, { title: "Delete role", confirmLabel: "Delete", danger: true }))) return;
    setBusy(true);
    try { await api.del(`/settings/rbac/roles/${role.id}`); onSaved(); }
    catch (e: any) { setErr(e?.message || "Couldn't delete the role"); setBusy(false); }
  }

  const Sw = ({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) => (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={onClick}
      className={`w-9 h-5 rounded-full flex-shrink-0 relative transition-colors ${on ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} /></button>
  );
  const Choice = ({ value, set, options }: { value: string; set: (v: string) => void; options: [string, string][] }) => (
    <div className="inline-flex rounded-xl bg-slate-100 p-1">
      {options.map(([v, l]) => (
        <button key={v} type="button" onClick={() => set(v)} aria-pressed={value === v}
          className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${value === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
      ))}
    </div>
  );
  const Row = ({ icon, title, hint, children }: { icon: string; title: string; hint?: string; children: React.ReactNode }) => (
    <div className="flex items-start gap-3 py-3.5 border-t border-slate-100 first:border-0">
      <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n={icon} c="text-base" /></span>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-ink-primary">{title}</div>
        {hint && <div className="text-[11px] text-ink-tertiary mb-2">{hint}</div>}
        {children}
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/30 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-label={isNew ? "New role" : `Edit ${name}`} className="relative w-[560px] max-w-full bg-white h-full shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.28s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="px-5 pt-5 pb-3">
          <div className="flex items-center gap-3">
            <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="badge" c="text-lg" /></span>
            <div className="flex-1 min-w-0">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Role name, like Front desk" autoFocus={isNew} aria-label="Role name"
                className="w-full text-lg font-semibold text-ink-primary bg-transparent outline-none placeholder:text-ink-tertiary placeholder:font-normal border-b border-transparent focus:border-slate-200 py-0.5" />
              <div className="text-[11px] text-ink-tertiary">{isNew && preset ? `Starts like ${preset}` : `Can do ${totalOn} of ${all.length} things`}</div>
            </div>
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
          </div>
          <div className="mt-4 inline-flex rounded-xl bg-slate-100 p-1">
            {([["can", "What they can do"], ["sees", "What they see"], ["home", "Their home screen"]] as [typeof tab, string][]).map(([id, l]) => (
              <button key={id} onClick={() => setTab(id)} aria-pressed={tab === id}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${tab === id ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pb-5 space-y-3">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

          {tab === "can" && (<>
            {isNew && (
              <div>
                <div className="text-[11px] font-semibold text-ink-tertiary mb-1.5">Start like</div>
                <div className="grid grid-cols-2 gap-2">
                  {PRESETS.map(([k, blurb, icon]) => (
                    <button key={k} type="button" onClick={() => { setPreset(preset === k ? null : k); setPerms({}); }} aria-pressed={preset === k}
                      className={`text-left rounded-xl border p-3 transition-colors ${preset === k ? "border-primary bg-primary/5 ring-2 ring-primary/15" : "border-slate-200 hover:border-slate-300"}`}>
                      <span className="flex items-center gap-2 text-xs font-semibold text-ink-primary"><M n={icon} c="text-sm text-primary" />{k}</span>
                      <span className="block text-[11px] text-ink-tertiary mt-0.5">{blurb}</span>
                    </button>
                  ))}
                </div>
                {preset && <p className="text-[11px] text-ink-tertiary mt-2">Changes you make below are added on top of {preset}. You can fine-tune everything after saving.</p>}
              </div>
            )}
            <div className="flex items-center gap-2 pt-1">
              <div className="relative flex-1">
                <M n="search" c="absolute left-3 top-2.5 text-base text-ink-tertiary pointer-events-none" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a permission…" aria-label="Find a permission"
                  className="w-full bg-slate-50 border border-transparent focus:border-slate-200 focus:bg-white rounded-xl pl-9 pr-3 py-2 text-sm outline-none" />
              </div>
              <button type="button" onClick={() => grantAll(all, true)} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 whitespace-nowrap">Allow all</button>
              <button type="button" onClick={() => grantAll(all, false)} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 whitespace-nowrap">Allow none</button>
            </div>
            {filtered.length === 0 && <div className="text-xs text-ink-tertiary py-4 text-center">Nothing matches "{q}".</div>}
            {filtered.map(([group, codes]) => {
              const isOpen = (open[group] ?? false) || !!ql;
              const live = codes;
              const onCount = live.filter((c) => allowed(c.code)).length;
              const allOn = live.length > 0 && onCount === live.length;
              return (
                <div key={group} className="rounded-2xl border border-slate-200 overflow-hidden">
                  <div className="flex items-center gap-3 px-4 py-3">
                    <button type="button" onClick={() => setOpen((o) => ({ ...o, [group]: !isOpen }))} aria-expanded={isOpen} className="flex items-center gap-2 min-w-0 flex-1 text-left">
                      <M n={isOpen ? "expand_more" : "chevron_right"} c="text-base text-ink-tertiary" />
                      <span className="text-sm text-ink-primary capitalize truncate">{group.replace(/_/g, " ")}</span>
                      {live.length > 0 && <span className={`text-[11px] px-1.5 py-0.5 rounded-md ${onCount === 0 ? "bg-slate-100 text-ink-tertiary" : allOn ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{allOn ? "All" : onCount === 0 ? "None" : `${onCount} of ${live.length}`}</span>}
                    </button>
                    {live.length > 0 && <Sw on={allOn} onClick={() => grantAll(live, !allOn)} label={`${allOn ? "Turn off" : "Turn on"} all ${group.replace(/_/g, " ")}`} />}
                  </div>
                  {isOpen && (
                    <div className="border-t border-slate-100 bg-slate-50/50">
                      {codes.map((c) => (
                        <div key={c.code} className="flex items-center gap-3 pl-11 pr-4 py-2.5 border-t border-slate-100 first:border-0">
                          <span className="text-xs flex-1 text-ink-primary">{c.label}</span>
                          <Sw on={allowed(c.code)} onClick={() => toggle(c.code)} label={c.label} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </>)}

          {tab === "sees" && (
            <div>
              <Row icon="assignment_late" title="Work orders">
                <Choice value={orderScope} set={setOrderScope} options={[["all", "All jobs"], ["own", "Only jobs assigned to them"]]} />
              </Row>
              <Row icon="contacts" title="Customers">
                <Choice value={contactScope} set={setContactScope} options={[["all", "All customers"], ["own", "Only their own"]]} />
              </Row>
              <Row icon="storefront" title="Locations" hint={locations.length < 2 ? "You have one location, so this only matters once you add another." : undefined}>
                <Choice value={locScope === "all" ? "all" : "some"} set={(v) => setLocScope(v === "all" ? "all" : "selected")} options={[["all", "All locations"], ["some", "Only some"]]} />
                {locScope !== "all" && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {locations.map((l) => { const on = locIds.includes(l.id); return (
                      <button key={l.id} type="button" onClick={() => setLocIds((s) => (on ? s.filter((x) => x !== l.id) : [...s, l.id]))} aria-pressed={on}
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border ${on ? "bg-white border-primary/40 text-ink-primary shadow-sm" : "border-dashed border-slate-300 text-ink-tertiary"}`}>
                        {on && <M n="check" c="text-[12px] text-primary" />}{l.name}
                      </button>
                    ); })}
                  </div>
                )}
              </Row>
              <Row icon="payments" title="Pay history" hint="How far back they can see payroll. Leave empty for all of it.">
                <div className="flex items-center gap-2">
                  <input type="number" min={0} value={payrollDays} onChange={(e) => setPayrollDays(e.target.value)} placeholder="All" aria-label="Days of pay history"
                    className="w-24 bg-white border border-slate-200 rounded-xl px-3 py-2 text-sm outline-none focus:border-primary" />
                  <span className="text-xs text-ink-secondary">days</span>
                </div>
              </Row>
              <Row icon="visibility_off" title="Hidden statuses" hint="Jobs in these statuses don't appear for this role.">
                {statuses.length === 0 ? <div className="text-xs text-ink-tertiary">No statuses yet.</div> : (
                  <div className="flex flex-wrap gap-1.5">
                    {statuses.map((s) => { const on = hiddenStatuses.includes(s.key); return (
                      <button key={s.key} type="button" onClick={() => setHiddenStatuses((h) => (on ? h.filter((k) => k !== s.key) : [...h, s.key]))} aria-pressed={on}
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border ${on ? "bg-rose-50 border-rose-200 text-rose-700" : "border-slate-200 text-ink-secondary hover:border-slate-300"}`}>
                        {on ? <M n="visibility_off" c="text-[12px]" /> : <span className="w-2 h-2 rounded-full" style={{ background: s.color }} />}{s.name}
                      </button>
                    ); })}
                  </div>
                )}
              </Row>
            </div>
          )}

          {tab === "home" && (
            <div>
              <Row icon="dashboard" title="Opens on" hint="Where someone with this role lands when they open the app.">
                <select value={homeRoute} onChange={(e) => setHomeRoute(e.target.value)} aria-label="Landing page"
                  className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-sm outline-none focus:border-primary">
                  {LANDING_PAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Row>
              <Row icon="notifications" title="Alerts on Home" hint="Pick what shows up. Pick none to show everything.">
                <div className="flex flex-wrap gap-1.5">
                  {FEED_SIGNALS.map(([key, label]) => { const on = feedSources.includes(key); return (
                    <button key={key} type="button" onClick={() => setFeedSources((s) => (on ? s.filter((k) => k !== key) : [...s, key]))} aria-pressed={on}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border ${on ? "bg-white border-primary/40 text-ink-primary shadow-sm" : "border-slate-200 text-ink-secondary hover:border-slate-300"}`}>
                      {on && <M n="check" c="text-[12px] text-primary" />}{label}
                    </button>
                  ); })}
                </div>
              </Row>
            </div>
          )}
        </div>

        <div className="px-5 py-4 border-t border-slate-100 flex items-center gap-2">
          {!isNew && (members > 0
            ? <span className="text-[11px] text-ink-tertiary">{members} {members === 1 ? "person has" : "people have"} this role</span>
            : <button onClick={remove} disabled={busy} className="px-3 py-2 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50">Delete role</button>)}
          <button onClick={onClose} className="ml-auto px-4 py-2 rounded-xl border border-slate-200 text-ink-secondary hover:bg-slate-50 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : isNew ? "Create role" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}


// How many devices one login may use at once. At the limit, signing in somewhere new asks the
// person to pick one of their devices to sign out; nobody is signed out without being asked.
const LIMITS: [string, string][] = [["", "No limit"], ["2", "2 devices"], ["3", "3 devices"], ["5", "5 devices"], ["10", "10 devices"]];

export function DeviceLimitCard({ onFlash }: { onFlash: (m: string) => void }) {
  const [value, setValue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<any>("/staff/company").then((c) => setValue(c?.max_sessions_per_login ? String(c.max_sessions_per_login) : "")).catch(() => setValue(""));
  }, []);
  async function save(v: string) {
    const before = value;
    setValue(v); setBusy(true);
    try {
      await api.put("/staff/company", { max_sessions_per_login: v ? Number(v) : null });
      onFlash(v ? `Each person can be signed in on ${v} devices at once` : "No device limit");
    } catch (e: any) { setValue(before); onFlash(e?.message || "Couldn't save"); }
    finally { setBusy(false); }
  }
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-5">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-[220px] flex-1">
          <label htmlFor="device-limit" className="block text-sm font-semibold text-ink-primary">Devices per person</label>
          <p className="mt-1 text-xs text-ink-tertiary">How many devices one login can use at the same time. When someone hits the limit,
            they pick a device to sign out before they can sign in somewhere new. The phone app counts as a device.</p>
        </div>
        <select id="device-limit" value={value ?? ""} disabled={value === null || busy} onChange={(e) => save(e.target.value)}
          className="glass-input rounded-xl px-3 py-2 text-sm font-semibold text-ink-primary">
          {LIMITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      <p className="mt-3 text-xs text-ink-tertiary">People already over a new limit stay signed in until they sign out.
        Each person can see and sign out their devices under Security &amp; sign-in.</p>
    </div>
  );
}
