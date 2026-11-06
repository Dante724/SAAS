import { useEffect, useMemo, useRef, useState } from "react";
import TeklySelect from "../../components/TeklySelect";
import { buttonClass } from "../../components/Button";
import DateField from "../../components/DateField";
import { M } from "../../components/Shell";
import Switch from "../../components/Switch";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import { loadFailed } from "../../lib/notice";

/* Settings › Locations — shops listed on the left, the chosen one edited in place on the right
   (no side panel). Opening hours are a week timeline: click a day to change it, or use a preset.
   Same endpoints as before (/staff/locations, /resources). */

export const DAYS: [string, string, string][] = [["mon", "Mon", "M"], ["tue", "Tue", "T"], ["wed", "Wed", "W"], ["thu", "Thu", "T"], ["fri", "Fri", "F"], ["sat", "Sat", "S"], ["sun", "Sun", "S"]];
type Day = { open?: string; close?: string; closed?: boolean };
type Hours = Record<string, Day>;

// The server books 09:00–18:00 every day when a location has no hours saved (public._day_window).
const DEFAULT_DAY: Day = { open: "09:00", close: "18:00" };
export const hoursOf = (wh: Hours | null | undefined): Hours =>
  wh && Object.keys(wh).length ? wh : Object.fromEntries(DAYS.map(([k]) => [k, { ...DEFAULT_DAY }]));

const PRESETS: [string, Hours][] = [
  ["Mon–Fri 9–6", Object.fromEntries(DAYS.map(([k], i) => [k, i < 5 ? { open: "09:00", close: "18:00" } : { closed: true }]))],
  ["Mon–Sat 9–6", Object.fromEntries(DAYS.map(([k], i) => [k, i < 6 ? { open: "09:00", close: "18:00" } : { closed: true }]))],
  ["Every day 10–7", Object.fromEntries(DAYS.map(([k]) => [k, { open: "10:00", close: "19:00" }]))],
];

function allZones(extra: string[]): string[] {
  let zones: string[] = [];
  try { zones = (Intl as any).supportedValuesOf?.("timeZone") || []; } catch { /* old browser */ }
  // Chrome still lists some zones under their old names; show the names people search for.
  const RENAMED: Record<string, string> = {
    "Asia/Calcutta": "Asia/Kolkata", "Asia/Katmandu": "Asia/Kathmandu", "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Rangoon": "Asia/Yangon", "Europe/Kiev": "Europe/Kyiv", "America/Godthab": "America/Nuuk",
    "Pacific/Enderbury": "Pacific/Kanton", "Atlantic/Faeroe": "Atlantic/Faroe", "Pacific/Truk": "Pacific/Chuuk", "Pacific/Ponape": "Pacific/Pohnpei",
  };
  zones = zones.map((z) => RENAMED[z] || z);
  return Array.from(new Set([...zones, "UTC", ...extra.filter(Boolean)])).sort();
}
const tzLabel = (z: string) => z.replace(/_/g, " ").replace(/^America\//, "").replace(/^Pacific\//, "");

// UTC offset of a zone right now, e.g. "UTC+5:30" (empty if the browser can't tell).
function tzOffset(z: string): string {
  try {
    const p = new Intl.DateTimeFormat("en-US", { timeZone: z, timeZoneName: "shortOffset" }).formatToParts(new Date()).find((x) => x.type === "timeZoneName");
    return (p?.value || "").replace(/^GMT/, "UTC").replace(/^UTC$/, "UTC+0");
  } catch { return ""; }
}

// A searchable time zone picker: the browser's own list holds 400+ zones and is unusable.
// Suggested zones (the company's, this location's, this computer's) come first.
function TimezonePicker({ value, onChange, zones, suggested, warn }: { value: string; onChange: (z: string) => void; zones: string[]; suggested: string[]; warn: boolean }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const top = Array.from(new Set(suggested.filter(Boolean)));
  const needle = q.trim().toLowerCase().replace(/\s+/g, "_");
  const match = (z: string) => !needle || z.toLowerCase().includes(needle) || tzOffset(z).toLowerCase().includes(needle);
  const list = (open ? zones.filter((z) => !top.includes(z) && match(z)) : []);
  const pick = (z: string) => { onChange(z); setOpen(false); setQ(""); };
  const row = (z: string) => (
    <button key={z} type="button" role="option" aria-selected={z === value} onClick={() => pick(z)}
      className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-left text-xs ${z === value ? "bg-primary/[0.06] text-primary font-semibold" : "text-ink-primary hover:bg-slate-50"}`}>
      <span className="flex-1 truncate">{z.replace(/_/g, " ")}</span>
      <span className="text-2xs text-ink-tertiary tabular-nums">{tzOffset(z)}</span>
      <M n="check" c={`text-xs ${z === value ? "" : "invisible"}`} />
    </button>
  );
  return (
    <div className="relative flex-shrink-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open} aria-label="Time zone"
        className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] max-w-[190px] ${warn ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-ink-secondary hover:text-ink-primary"}`}>
        <M n="public" c="text-[12px]" /><span className="truncate">{value.replace(/_/g, " ")}</span><M n="expand_more" c="text-xs" />
      </button>
      {open && (<>
        <div className="fixed inset-0 z-30" aria-hidden="true" onClick={() => { setOpen(false); setQ(""); }} />
        <div className="absolute right-0 top-full mt-1.5 z-40 w-80 bg-white rounded-2xl border border-slate-200/90 shadow-drawer p-1.5"
          onKeyDown={(e) => { if (e.key === "Escape") { setOpen(false); setQ(""); } }}>
          <div className="relative p-1">
            <M n="search" c="absolute left-3.5 top-1/2 -translate-y-1/2 text-sm text-ink-tertiary pointer-events-none" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a city or offset (e.g. Kolkata, +5:30)" aria-label="Search time zones"
              onKeyDown={(e) => { if (e.key === "Enter") { const first = [...top.filter(match), ...list][0]; if (first) pick(first); } }}
              className="glass-input w-full h-9 pl-8 pr-3 rounded-xl text-sm" />
          </div>
          <div role="listbox" aria-label="Time zones" className="max-h-72 overflow-y-auto mt-1">
            {top.filter(match).length > 0 && (<>
              <p className="px-3 pt-1.5 pb-1 text-2xs font-semibold text-ink-tertiary">Suggested</p>
              {top.filter(match).map(row)}
              <div className="my-1 border-t border-slate-100" />
            </>)}
            {list.map(row)}
            {top.filter(match).length + list.length === 0 && <p className="px-3 py-3 text-xs text-ink-tertiary">No time zone matches “{q}”.</p>}
          </div>
        </div>
      </>)}
    </div>
  );
}

/** Local weekday key + "HH:MM" for a time zone, or null when the zone is unknown. */
function nowIn(tz: string): { day: string; hm: string } | null {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz || "UTC", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date());
    const get = (t: string) => parts.find((p) => p.type === t)?.value || "";
    return { day: get("weekday").slice(0, 3).toLowerCase(), hm: `${get("hour")}:${get("minute")}` };
  } catch { return null; }
}
const to12 = (hm?: string) => {
  if (!hm) return ""; const [h, m] = hm.split(":").map(Number);
  return `${((h + 11) % 12) + 1}${m ? `:${String(m).padStart(2, "0")}` : ""}${h < 12 ? "am" : "pm"}`;
};

const inp = "glass-input w-full rounded-xl px-3 py-2 text-sm text-ink-primary";
const toMin = (hm?: string) => { const [h, m] = (hm || "0:0").split(":").map(Number); return (h || 0) * 60 + (m || 0); };
// Timeline scale: 6am–10pm, widened if a shop opens earlier or closes later.
function scaleFor(wh: Hours): [number, number] {
  let lo = 360, hi = 1320;
  for (const [k] of DAYS) { const d = wh[k]; if (d && !d.closed) { lo = Math.min(lo, Math.floor(toMin(d.open) / 60) * 60); hi = Math.max(hi, Math.ceil(toMin(d.close) / 60) * 60); } }
  return [lo, hi];
}
const statusOf = (l: any) => {
  const wh = hoursOf(l.working_hours); const now = nowIn(l.timezone || "UTC"); const d = now ? wh[now.day] : undefined;
  if (!now || !d || d.closed) return { open: false, text: "Closed now" };
  if (now.hm < (d.open || "")) return { open: false, text: `Opens ${to12(d.open)}` };
  if (now.hm < (d.close || "")) return { open: true, text: `Open until ${to12(d.close)}` };
  return { open: false, text: "Closed now" };
};

export default function LocationsPage({ companyTz }: { companyTz?: string }) {
  const [items, setItems] = useState<any[] | null>(null);
  const [res, setRes] = useState<any[]>([]);
  const [sel, setSel] = useState<string | null>(null);   // location id, or "new"
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2000); };
  const load = (pick?: string) => {
    api.get<any>("/staff/locations").then((r) => {
      const list = r.items || r || []; setItems(list);
      setSel((cur) => pick || (cur && (cur === "new" || list.some((x: any) => x.id === cur)) ? cur : list.find((x: any) => x.active !== false)?.id || null));
    }).catch(() => setItems([]));
    api.get<any>("/resources").then((r) => setRes(r.items || [])).catch(() => setRes([]));
  };
  useEffect(() => { load(); }, []);  // eslint-disable-line
  const active = (items || []).filter((l) => l.active !== false);
  const archived = (items || []).filter((l) => l.active === false);
  const cur = sel === "new" ? null : (items || []).find((l) => l.id === sel) || null;

  async function reactivate(l: any) {
    try { await api.put(`/staff/locations/${l.id}`, { name: l.name, active: true }); flash(`${l.name} is back`); load(l.id); }
    catch (e: any) { flash(e?.message || "Couldn't reactivate"); }
  }

  return (
    <div className="max-w-5xl pb-16">
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
      <div className="mb-5">
        <h2 className="text-base font-semibold text-ink-primary">Locations</h2>
        <p className="text-xs text-ink-tertiary">Your shops, their opening hours (customers can only book inside them), time zone and bays.</p>
      </div>
      {items == null ? <div className="text-xs text-ink-tertiary">Loading…</div> : (
        <div className="grid md:grid-cols-[220px_minmax(0,1fr)] gap-4 items-start">
          <nav className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-1.5" aria-label="Locations">
            {active.map((l) => { const st = statusOf(l); const on = sel === l.id; return (
              <button key={l.id} onClick={() => setSel(l.id)} aria-current={on ? "true" : undefined}
                className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-left transition-colors ${on ? "bg-primary/10" : "hover:bg-slate-50"}`}>
                <M n="storefront" c={`text-base ${on ? "text-primary" : "text-ink-tertiary"}`} />
                <span className="min-w-0">
                  <span className={`block text-sm truncate ${on ? "font-semibold text-ink-primary" : "text-ink-primary"}`}>{l.name}</span>
                  <span className="flex items-center gap-1 text-[11px] text-ink-tertiary"><span className={`w-1.5 h-1.5 rounded-full ${st.open ? "bg-emerald-500" : "bg-slate-300"}`} />{st.text}</span>
                </span>
              </button>
            ); })}
            <button onClick={() => setSel("new")} className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-left text-sm font-semibold ${sel === "new" ? "bg-primary/10 text-primary" : "text-primary hover:bg-primary/5"}`}>
              <M n="add" c="text-base" />Add location
            </button>
            {archived.length > 0 && (
              <div className="mt-1 pt-2 border-t border-slate-100 px-3 pb-1.5">
                <div className="text-[11px] font-semibold text-ink-tertiary mb-1">Archived</div>
                {archived.map((l) => (
                  <div key={l.id} className="flex items-center gap-2 py-1 text-xs text-ink-secondary">
                    <span className="truncate flex-1">{l.name}</span>
                    <button onClick={() => reactivate(l)} className="text-[11px] font-semibold text-primary">Bring back</button>
                  </div>
                ))}
              </div>
            )}
          </nav>
          {(cur || sel === "new") && (
            <LocationDetail key={sel || ""} loc={cur} companyTz={companyTz} resources={res.filter((r) => cur && r.location_id === cur.id)}
              onSaved={(m, id) => { flash(m); load(id); }} onResChanged={() => api.get<any>("/resources").then((r) => setRes(r.items || [])).catch(loadFailed("resources"))}
              onCancelNew={() => setSel(active[0]?.id || null)} />
          )}
        </div>
      )}
    </div>
  );
}

function LocationDetail({ loc, companyTz, resources, onSaved, onResChanged, onCancelNew }: {
  loc: any | null; companyTz?: string; resources: any[]; onSaved: (m: string, id?: string) => void; onResChanged: () => void; onCancelNew: () => void;
}) {
  const init = () => ({
    name: loc?.name || "", phone: loc?.phone || "", address: loc?.address || "",
    timezone: loc?.timezone || companyTz || "UTC", receipt_header: loc?.receipt_header || "",
    legal_entity_id: loc?.legal_entity_id || "", price_type_id: loc?.price_type_id || "",
  });
  const [f, setF] = useState<any>(init);
  const [wh, setWh] = useState<Hours>(() => hoursOf(loc?.working_hours));
  const orig = useRef(JSON.stringify([init(), hoursOf(loc?.working_hours)]));
  const [day, setDay] = useState<string | null>(null);
  const [panel, setPanel] = useState<"bays" | "billing" | null>(null);
  const [legals, setLegals] = useState<any[]>([]);
  const [priceTypes, setPriceTypes] = useState<any[]>([]);
  const [nr, setNr] = useState({ name: "", kind: "bay" });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const zones = useMemo(() => allZones([f.timezone, companyTz || ""]), []);  // eslint-disable-line
  useEffect(() => {
    api.get<any>("/settings/legal-entities").then((r) => setLegals(r.items || r || [])).catch(loadFailed("legal entities"));
    api.get<any>("/pricing/types").then((r) => setPriceTypes(r.items || r || [])).catch(loadFailed("price types"));
  }, []);
  const isNew = !loc;
  const dirty = isNew || JSON.stringify([f, wh]) !== orig.current;
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const patchDay = (k: string, p: Day) => setWh((s) => ({ ...s, [k]: { ...(s[k] || DEFAULT_DAY), ...p } }));
  const bad = DAYS.filter(([k]) => { const d = wh[k] || {}; return !d.closed && !!d.open && !!d.close && d.close <= d.open; }).map(([, l]) => l);
  const [lo, hi] = scaleFor(wh);
  const pct = (m: number) => `${Math.max(0, Math.min(100, ((m - lo) / (hi - lo)) * 100))}%`;
  const now = loc ? nowIn(f.timezone) : null;
  const legalName = legals.find((x) => x.id === f.legal_entity_id)?.name;
  const priceName = priceTypes.find((x) => x.id === f.price_type_id)?.name;

  async function save() {
    if (!f.name.trim()) { setErr("Give the location a name"); return; }
    if (bad.length) { setErr(`Closing time must be after opening time: ${bad.join(", ")}`); return; }
    setBusy(true); setErr(null);
    const body = { ...f, name: f.name.trim(), legal_entity_id: f.legal_entity_id || null, price_type_id: f.price_type_id || null,
      working_hours: Object.fromEntries(DAYS.map(([k]) => [k, wh[k]?.closed ? { closed: true } : { open: wh[k]?.open || "09:00", close: wh[k]?.close || "18:00" }])) };
    try {
      if (loc?.id) { await api.put(`/staff/locations/${loc.id}`, body); orig.current = JSON.stringify([f, wh]); onSaved("Location saved", loc.id); }
      else { const r = await api.post<any>("/staff/locations", body); onSaved("Location added", r?.id); }
    } catch (e: any) { setErr(e?.message || "Couldn't save"); } finally { setBusy(false); }
  }
  async function archive() {
    if (!loc?.id || !(await confirmDialog(`Archive ${loc.name}? It's hidden from pickers and booking. Orders and history stay, and you can bring it back.`, { title: "Archive location", danger: true, confirmLabel: "Archive" }))) return;
    try { await api.del(`/staff/locations/${loc.id}`); onSaved("Location archived"); } catch (e: any) { setErr(e?.message || "Couldn't archive"); }
  }
  async function addRes() {
    if (!nr.name.trim() || !loc?.id) return;
    try { await api.post("/resources", { name: nr.name.trim(), kind: nr.kind, color: "#2cd4c0", location_id: loc.id }); setNr({ name: "", kind: nr.kind }); onResChanged(); }
    catch (e: any) { setErr(e?.message || "Couldn't add it"); }
  }
  async function delRes(r: any) {
    if (!(await confirmDialog(`Remove "${r.name}"? Bookings already in it stay.`, { title: "Remove", danger: true, confirmLabel: "Remove" }))) return;
    try { await api.del(`/resources/${r.id}`); onResChanged(); } catch (e: any) { setErr(e?.message || "Couldn't remove it"); }
  }

  const ghost = "w-full bg-transparent border border-transparent hover:border-slate-200 focus:border-primary focus:bg-white rounded-lg px-2 py-1 outline-none";
  return (
    <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
      <div className="px-5 pt-5 pb-4 border-b border-slate-100">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0 -ml-2">
            <input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Location name, like Downtown" autoFocus={isNew} aria-label="Location name"
              className={`${ghost} text-lg font-semibold text-ink-primary placeholder:font-normal placeholder:text-ink-tertiary`} />
            <div className="grid sm:grid-cols-[minmax(0,1fr)_170px] gap-1">
              <input value={f.address} onChange={(e) => set("address", e.target.value)} placeholder="Street address, city, state" aria-label="Address" className={`${ghost} text-xs text-ink-secondary`} />
              <input value={f.phone} onChange={(e) => set("phone", e.target.value)} placeholder="Phone" aria-label="Phone" className={`${ghost} text-xs text-ink-secondary`} />
            </div>
          </div>
          <TimezonePicker value={f.timezone} onChange={(z) => set("timezone", z)} zones={zones}
            suggested={[companyTz || "", f.timezone, (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return ""; } })()]}
            warn={!!companyTz && f.timezone !== companyTz} />
        </div>
        {companyTz && f.timezone !== companyTz && (
          <div className="mt-2 text-[11px] text-amber-700">Your company uses {tzLabel(companyTz)}, so booking times here may be off. <button type="button" onClick={() => set("timezone", companyTz)} className="font-semibold underline">Use {tzLabel(companyTz)}</button></div>
        )}
      </div>

      <div className="px-5 py-4">
        <div className="flex items-center flex-wrap gap-2 mb-3">
          <span className="text-sm font-semibold text-ink-primary">Opening hours</span>
          {!loc?.working_hours && !isNew && <span className="text-[11px] text-ink-tertiary">Standard hours until you change them</span>}
          <span className="ml-auto flex flex-wrap gap-1">
            {PRESETS.map(([l, h]) => (
              <button key={l} type="button" onClick={() => { setWh(JSON.parse(JSON.stringify(h))); setDay(null); }} className="px-2.5 py-1 rounded-full text-[11px] font-semibold text-primary hover:bg-primary/5">{l}</button>
            ))}
          </span>
        </div>
        <div className="grid grid-cols-[40px_minmax(0,1fr)] gap-x-3 gap-y-1.5 items-center">
          <span />
          <div className="relative h-4 text-[11px] text-ink-tertiary">
            {Array.from({ length: Math.floor((hi - lo) / 180) + 1 }, (_, i) => lo + i * 180).map((m) => (
              <span key={m} className="absolute -translate-x-1/2" style={{ left: pct(m) }}>{to12(`${Math.floor(m / 60)}:00`)}</span>
            ))}
          </div>
          {DAYS.map(([k, l]) => {
            const d = wh[k] || DEFAULT_DAY; const open = !d.closed; const isToday = now?.day === k; const editing = day === k;
            const wrong = open && !!d.open && !!d.close && d.close <= d.open;
            return [
              <span key={`${k}-l`} className={`self-start pt-2 text-xs ${isToday ? "font-semibold text-ink-primary" : "text-ink-secondary"}`}>{l}</span>,
              <div key={`${k}-b`}>
                <button type="button" onClick={() => setDay(editing ? null : k)} aria-expanded={editing} aria-label={`${l}: ${open ? `${to12(d.open)} to ${to12(d.close)}` : "closed"}`}
                  className={`relative w-full h-8 rounded-lg transition-colors ${editing ? "bg-primary/5 ring-2 ring-primary/30" : "bg-slate-50 hover:bg-slate-100"} ${isToday && !editing ? "ring-1 ring-slate-300" : ""}`}>
                  {open && !wrong ? (
                    <span className="absolute top-1 bottom-1 rounded-md bg-primary/15 border border-primary/30 flex items-center justify-center text-[11px] font-semibold text-primary overflow-hidden whitespace-nowrap"
                      style={{ left: pct(toMin(d.open)), width: `calc(${pct(toMin(d.close))} - ${pct(toMin(d.open))})` }}>{to12(d.open)}–{to12(d.close)}</span>
                  ) : <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[11px] text-ink-tertiary">{wrong ? <span className="text-rose-600">Closes before it opens</span> : "Closed"}</span>}
                  {isToday && now && open && (() => { const m = toMin(now.hm); return m >= lo && m <= hi ? <span className="absolute top-0 bottom-0 w-px bg-rose-400" style={{ left: pct(m) }} /> : null; })()}
                </button>
                {editing && (
                  <div className="flex flex-wrap items-center gap-2 mt-1.5 mb-1 px-1">
                    <Switch size="sm" label={`Open on ${l}`} on={open} onChange={(v) => patchDay(k, { closed: !v })} />
                    <span className="text-xs text-ink-secondary w-12">{open ? "Open" : "Closed"}</span>
                    {open && (<>
                      <DateField mode="time" className="w-36" value={d.open || "09:00"} onChange={(v) => patchDay(k, { open: v })} label={`${l} opens`} />
                      <span className="text-xs text-ink-tertiary">to</span>
                      <DateField mode="time" className="w-36" value={d.close || "18:00"} onChange={(v) => patchDay(k, { close: v })} label={`${l} closes`} />
                    </>)}
                    <button type="button" onClick={() => setWh((s) => Object.fromEntries(DAYS.map(([kk], i) => [kk, i < 5 ? { ...d } : (s[kk] || DEFAULT_DAY)])))}
                      className="ml-auto text-[11px] font-semibold text-ink-tertiary hover:text-ink-secondary">Same for Mon–Fri</button>
                  </div>
                )}
              </div>,
            ];
          })}
        </div>
      </div>

      <div className="px-5 pb-5 grid sm:grid-cols-2 gap-3">
        <button type="button" onClick={() => setPanel(panel === "bays" ? null : "bays")} aria-expanded={panel === "bays"}
          className={`text-left rounded-xl px-4 py-3 transition-colors ${panel === "bays" ? "bg-primary/5 ring-1 ring-primary/20" : "bg-slate-50 hover:bg-slate-100"}`}>
          <div className="flex items-center gap-1.5 text-[11px] text-ink-tertiary"><M n="view_column" c="text-[13px]" />Bays and rooms</div>
          <div className="text-sm text-ink-primary mt-0.5 truncate">{isNew ? "Add after saving" : resources.length ? resources.map((r) => r.name).join(" · ") : "None yet"}</div>
        </button>
        <button type="button" onClick={() => setPanel(panel === "billing" ? null : "billing")} aria-expanded={panel === "billing"}
          className={`text-left rounded-xl px-4 py-3 transition-colors ${panel === "billing" ? "bg-primary/5 ring-1 ring-primary/20" : "bg-slate-50 hover:bg-slate-100"}`}>
          <div className="flex items-center gap-1.5 text-[11px] text-ink-tertiary"><M n="receipt_long" c="text-[13px]" />Invoices and pricing</div>
          <div className="text-sm text-ink-primary mt-0.5 truncate">{legalName || priceName || f.receipt_header ? [legalName, priceName].filter(Boolean).join(" · ") || "Custom receipt line" : "Company default"}</div>
        </button>
        {panel === "bays" && (
          <div className="sm:col-span-2 rounded-xl border border-slate-200 p-4 space-y-3">
            {isNew ? <div className="text-xs text-ink-tertiary">Save the location first, then add its bays and rooms.</div> : (<>
              <div className="flex flex-wrap gap-1.5">
                {resources.map((r) => (
                  <span key={r.id} className="inline-flex items-center gap-1.5 pl-2.5 pr-1 py-1 rounded-full bg-slate-100 text-xs text-ink-primary">
                    <span className="w-2 h-2 rounded-full" style={{ background: r.color || "#2cd4c0" }} />{r.name}
                    <button aria-label={`Remove ${r.name}`} onClick={() => delRes(r)} className="w-5 h-5 rounded-full grid place-items-center hover:bg-slate-200"><M n="close" c="text-[11px]" /></button>
                  </span>
                ))}
                {!resources.length && <span className="text-xs text-ink-tertiary">Add the lifts, benches or rooms you schedule work into.</span>}
              </div>
              <div className="flex items-center gap-2">
                <input value={nr.name} onChange={(e) => setNr({ ...nr, name: e.target.value })} onKeyDown={(e) => e.key === "Enter" && addRes()} placeholder="Lift 1, Bench 2, Room A" aria-label="Bay or room name" className={inp} />
                <TeklySelect value={nr.kind} onChange={(e) => setNr({ ...nr, kind: e.target.value })} aria-label="Kind" className="glass-input rounded-xl px-3 py-2 text-sm">
                  {[["bay", "Bay / lift"], ["room", "Room"], ["equipment", "Equipment"], ["other", "Other"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </TeklySelect>
                <button onClick={addRes} disabled={!nr.name.trim()} className={buttonClass("primary", "md")}>Add</button>
              </div>
            </>)}
          </div>
        )}
        {panel === "billing" && (
          <div className="sm:col-span-2 rounded-xl border border-slate-200 p-4 space-y-3">
            <div className="grid sm:grid-cols-2 gap-3">
              <label className="block"><span className="block text-sm font-medium text-ink-primary mb-1.5">Invoices are from</span>
                <TeklySelect value={f.legal_entity_id || ""} onChange={(e) => set("legal_entity_id", e.target.value)} className={inp}><option value="">Company default</option>{legals.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</TeklySelect></label>
              <label className="block"><span className="block text-sm font-medium text-ink-primary mb-1.5">Price level</span>
                <TeklySelect value={f.price_type_id || ""} onChange={(e) => set("price_type_id", e.target.value)} className={inp}><option value="">Company default</option>{priceTypes.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</TeklySelect></label>
            </div>
            <label className="block"><span className="block text-sm font-medium text-ink-primary mb-1.5">Line at the top of receipts</span>
              <input value={f.receipt_header} onChange={(e) => set("receipt_header", e.target.value)} placeholder="Thanks for visiting our Downtown shop!" className={inp} /></label>
          </div>
        )}
      </div>

      {err && <div className="mx-5 mb-4 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      <div className="px-5 py-3.5 border-t border-slate-100 flex items-center gap-2">
        {loc?.id && <button onClick={archive} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-tertiary hover:text-rose-600 hover:bg-rose-50">Archive location</button>}
        {isNew && <button onClick={onCancelNew} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Cancel</button>}
        <span className="ml-auto text-[11px] text-ink-tertiary">{dirty && !isNew ? "Unsaved changes" : ""}</span>
        {!isNew && dirty && <button onClick={() => { const i = JSON.parse(orig.current); setF(i[0]); setWh(i[1]); setDay(null); }} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Discard</button>}
        <button onClick={save} disabled={busy || !dirty} className={buttonClass("primary", "md")}>{busy ? "Saving…" : isNew ? "Add location" : "Save"}</button>
      </div>
    </section>
  );
}
