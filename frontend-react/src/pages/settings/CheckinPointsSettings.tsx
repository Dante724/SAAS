import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api, openAuthedDoc } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import { printHtmlInFrame } from "../../lib/safeHtml";
import { useMe } from "../../context/Me";

// Settings › Public pages › Self check-in: the check-in points of each location. Every point has
// its own QR (unique, replaceable), a mode and the job types it offers; the form itself comes
// from the industry template plus the job-type fields ticked "Ask at check-in" (Settings › Forms).

export type Point = {
  id: string; location_id: string; location: string; name: string; mode: "walk_in" | "key_drop" | "auto";
  kiosk: boolean; job_type_ids: string[]; welcome_text: string; terms_template_id?: string | null;
  require_photo: boolean; show_wait: boolean; active: boolean; scans: number; submits: number; url: string; path: string;
};
type JobType = { id: string; name: string };
type Loc = { id: string; name: string };

const MODES: [Point["mode"], string][] = [
  ["auto", "Automatic"], ["walk_in", "Walk-in queue"], ["key_drop", "Key drop only"],
];
const MODE_HINT: Record<Point["mode"], string> = {
  auto: "Walk-in queue while open, key drop when closed",
  walk_in: "Walk-in queue",
  key_drop: "Key drop only",
};

function Switch({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition disabled:opacity-50 ${on ? "bg-primary" : "bg-slate-300"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

// One setting: label and why on the left, its control on the right.
function Row({ label, help, htmlFor, children }: { label: string; help?: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-6 py-3">
      <div className="min-w-0 flex-1">
        <label htmlFor={htmlFor} className="block text-sm font-medium text-ink-primary">{label}</label>
        {help && <p className="mt-0.5 text-xs text-ink-tertiary">{help}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function PointRow({ p, types, canEdit, onSaved, onFlash }: { p: Point; types: JobType[]; canEdit: boolean; onSaved: (p: Point) => void; onFlash: (m: string) => void }) {
  const [f, setF] = useState<Point>(p);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => setF(p), [p]);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [menu]);
  const dirty = JSON.stringify(f) !== JSON.stringify(p);
  const set = (patch: Partial<Point>) => setF((x) => ({ ...x, ...patch }));

  async function run(fn: () => Promise<Point>, msg: string) {
    setBusy(true);
    try { onSaved(await fn()); onFlash(msg); } catch (e: any) { onFlash(e?.message || "Couldn't save"); } finally { setBusy(false); }
  }
  const save = () => run(() => api.put<Point>(`/checkins/points/${p.id}`, f), "Check-in point saved");
  async function rotate() {
    setMenu(false);
    if (!(await confirmDialog("The current QR stops working straight away. Print the new poster and replace the old one. People already in the queue keep their links.", { title: "Replace this QR code?", confirmLabel: "Replace code" }))) return;
    run(() => api.post<Point>(`/checkins/points/${p.id}/rotate`, {}), "New code ready. Print the new poster.");
  }
  async function poster() {
    try { const r = await api.get<{ html: string }>(`/checkins/points/${p.id}/poster`); printHtmlInFrame(r.html); }
    catch (e: any) { onFlash(e?.message || "Couldn't prepare the poster"); }
  }
  async function copy() {
    setMenu(false);
    try { await navigator.clipboard.writeText(p.url || window.location.origin + p.path); onFlash("Link copied"); } catch { onFlash(p.url || p.path); }
  }
  const item = "w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-ink-primary hover:bg-slate-50 text-left";
  const inp = "glass-input rounded-xl px-3 py-2 text-sm";

  return (
    <div className={p.active ? "" : "bg-slate-50/60"}>
      <div className="flex items-center gap-3 px-5 py-4">
        <span className={`h-10 w-10 shrink-0 rounded-xl grid place-items-center ${p.active ? "bg-primary/10 text-primary" : "bg-slate-100 text-ink-tertiary"}`}><M n="qr_code_2" c="text-xl" /></span>
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="min-w-0 flex-1 text-left">
          <span className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink-primary truncate">{p.name}</span>
            <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-md ${p.active ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-ink-tertiary"}`}>{p.active ? "Active" : "Off"}</span>
          </span>
          <span className="block text-xs text-ink-tertiary truncate">{MODE_HINT[p.mode]} · {p.scans} scans · {p.submits} check-ins</span>
        </button>
        <button onClick={poster} className="h-9 px-3 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1.5"><M n="print" c="text-sm" />Print poster</button>
        <div ref={menuRef} className="relative">
          <button type="button" aria-label={`More for ${p.name}`} aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}
            className="h-9 w-9 rounded-xl border border-slate-200 bg-white grid place-items-center text-ink-secondary hover:bg-slate-50"><M n="more_horiz" c="text-base" /></button>
          {menu && (
            <div role="menu" className="absolute right-0 top-full mt-1.5 z-40 w-52 bg-raised rounded-xl border border-slate-200/80 shadow-clay-card p-1">
              <button role="menuitem" className={item} onClick={() => { setMenu(false); openAuthedDoc(`/api/checkins/points/${p.id}/qr.svg`).catch(() => onFlash("Couldn't download the QR")); }}><M n="download" c="text-base text-ink-tertiary" />Download QR</button>
              <button role="menuitem" className={item} onClick={copy}><M n="link" c="text-base text-ink-tertiary" />Copy link</button>
              <a role="menuitem" className={item} href={p.path} target="_blank" rel="noopener noreferrer" onClick={() => setMenu(false)}><M n="open_in_new" c="text-base text-ink-tertiary" />Preview</a>
              {canEdit && <><div className="my-1 border-t border-slate-100" />
                <button role="menuitem" className={`${item} text-rose-600 hover:bg-rose-50`} onClick={rotate} disabled={busy}><M n="refresh" c="text-base" />Replace QR code</button></>}
            </div>
          )}
        </div>
        <button type="button" aria-label={open ? `Close ${p.name}` : `Edit ${p.name}`} onClick={() => setOpen((o) => !o)}
          className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 flex items-center gap-1">
          {open ? "Close" : "Edit"}<M n={open ? "expand_less" : "expand_more"} c="text-base" />
        </button>
      </div>

      {open && (
        <div className="px-5 pb-4">
          <div className="rounded-xl border border-slate-200/80 bg-white px-4 divide-y divide-slate-100">
            <Row label="Name" htmlFor={`name-${p.id}`} help="Printed on the poster, e.g. Front door or Key box.">
              <input id={`name-${p.id}`} aria-label="Point name" value={f.name} disabled={!canEdit} onChange={(e) => set({ name: e.target.value })} className={`${inp} w-56`} />
            </Row>
            <Row label="How it works" htmlFor={`mode-${p.id}`} help={f.mode === "auto" ? "Walk-in queue while you're open, key drop when you're closed." : undefined}>
              <select id={`mode-${p.id}`} value={f.mode} disabled={!canEdit} onChange={(e) => set({ mode: e.target.value as Point["mode"] })} className={`${inp} w-56`}>
                {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </Row>
            <div className="py-3">
              <div className="text-sm font-medium text-ink-primary">Job types offered</div>
              <p className="mt-0.5 text-xs text-ink-tertiary">{types.length === 0 ? "Add job types in Settings › Forms." : "None selected uses the default job type."}</p>
              {types.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">
                {types.map((t) => {
                  const on = f.job_type_ids.includes(t.id);
                  return (
                    <label key={t.id} className={`cursor-pointer h-8 px-3 rounded-full border text-xs font-semibold flex items-center gap-1.5 transition ${on ? "border-primary bg-primary/5 text-primary" : "border-slate-200 text-ink-secondary hover:bg-slate-50"} ${!canEdit ? "pointer-events-none opacity-60" : ""}`}>
                      <input type="checkbox" className="sr-only" disabled={!canEdit} checked={on}
                        onChange={(e) => set({ job_type_ids: e.target.checked ? [...f.job_type_ids, t.id] : f.job_type_ids.filter((x) => x !== t.id) })} />
                      {on && <M n="check" c="text-sm" />}{t.name}
                    </label>
                  );
                })}
              </div>}
            </div>
            <div className="py-3">
              <label htmlFor={`welcome-${p.id}`} className="block text-sm font-medium text-ink-primary">Welcome text <span className="font-normal text-ink-tertiary">(optional)</span></label>
              <input id={`welcome-${p.id}`} value={f.welcome_text} disabled={!canEdit} onChange={(e) => set({ welcome_text: e.target.value })} placeholder="Shown under the heading" className={`${inp} mt-2 w-full`} />
            </div>
            {/* Only the options this mode can use: a key-drop-only point has no queue, a walk-in-only point no key drops. */}
            {f.mode !== "key_drop" && <Row label="Counter tablet" help="Clears after each customer; staff sign in to exit.">
              <Switch label="Counter tablet" disabled={!canEdit} on={f.kiosk} onChange={(v) => set({ kiosk: v })} /></Row>}
            {f.mode !== "key_drop" && <Row label="Show estimated wait">
              <Switch label="Show estimated wait" disabled={!canEdit} on={f.show_wait} onChange={(v) => set({ show_wait: v })} /></Row>}
            {f.mode !== "walk_in" && <Row label="Dashboard photo for key drops" help="The customer photographs the dashboard when leaving keys.">
              <Switch label="Key drop needs a dashboard photo" disabled={!canEdit} on={f.require_photo} onChange={(v) => set({ require_photo: v })} /></Row>}
            <Row label="Active" help="Off: scanning this QR asks the customer to see the counter.">
              <Switch label="Active" disabled={!canEdit} on={f.active} onChange={(v) => set({ active: v })} /></Row>
          </div>
          {canEdit && (
            <div className="mt-3 flex items-center gap-2">
              <span className="ml-auto text-[11px] text-ink-tertiary">{dirty ? "Unsaved changes" : ""}</span>
              {dirty && <button onClick={() => setF(p)} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Discard</button>}
              <button onClick={save} disabled={!dirty || busy} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-40">{busy ? "Saving…" : "Save"}</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function CheckinPointsSettings() {
  const { can } = useMe();
  const canEdit = can("can_manage_settings");
  const [points, setPoints] = useState<Point[] | null>(null);
  const [types, setTypes] = useState<JobType[]>([]);
  const [locs, setLocs] = useState<Loc[]>([]);
  const [msg, setMsg] = useState("");
  const [add, setAdd] = useState<{ location_id: string; name: string; mode: Point["mode"] } | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 3000); };

  function reload() { api.get<{ items: Point[] }>("/checkins/points").then((r) => setPoints(r.items || [])).catch(() => setPoints([])); }
  useEffect(() => {
    reload();
    api.get<{ items: JobType[] }>("/forms/types").then((r) => setTypes(r.items || [])).catch(() => setTypes([]));
    api.get<{ items: Loc[] }>("/staff/locations").then((r) => setLocs(r.items || [])).catch(() => setLocs([]));
  }, []);

  async function create() {
    if (!add || !add.name.trim()) return;
    try { await api.post("/checkins/points", add); setAdd(null); reload(); flash("Check-in point added"); }
    catch (e: any) { flash(e?.message || "Couldn't add it"); }
  }

  const byLoc = new Map<string, Point[]>();
  for (const p of points || []) byLoc.set(p.location || "", [...(byLoc.get(p.location || "") || []), p]);

  return (
    <section className="max-w-4xl space-y-4 pb-10">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1 basis-64">
          <h2 className="text-base font-semibold text-ink-primary">Self check-in</h2>
          <p className="mt-0.5 text-sm text-ink-tertiary">Customers scan a QR at the door, key box or counter and check themselves in. Check-ins appear in Inquiries › Walk-ins.</p>
        </div>
        {canEdit && <button onClick={() => setAdd({ location_id: locs[0]?.id || "", name: "", mode: "walk_in" })} className="ml-auto h-9 px-3.5 rounded-xl bg-ink-primary text-white text-xs font-semibold flex-shrink-0 flex items-center gap-1.5"><M n="add" c="text-sm" />Add check-in point</button>}
      </div>
      {msg && <p role="status" className="text-xs text-ink-secondary">{msg}</p>}
      {add && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 flex flex-wrap items-end gap-2 text-xs">
          <div><label htmlFor="new-pt-loc" className="block font-semibold text-ink-secondary mb-1">Location</label>
            <select id="new-pt-loc" value={add.location_id} onChange={(e) => setAdd({ ...add, location_id: e.target.value })} className="glass-input px-3 py-2 rounded-lg">{locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
          <div><label htmlFor="new-pt-name" className="block font-semibold text-ink-secondary mb-1">Name</label>
            <input id="new-pt-name" value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} placeholder="Key box" className="glass-input px-3 py-2 rounded-lg" /></div>
          <div><label htmlFor="new-pt-mode" className="block font-semibold text-ink-secondary mb-1">How it works</label>
            <select id="new-pt-mode" value={add.mode} onChange={(e) => setAdd({ ...add, mode: e.target.value as Point["mode"] })} className="glass-input px-3 py-2 rounded-lg">{MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          <button onClick={create} disabled={!add.name.trim()} className="h-9 px-4 rounded-lg bg-primary text-white font-bold disabled:opacity-40">Add</button>
          <button onClick={() => setAdd(null)} className="h-9 px-3 rounded-lg text-ink-secondary">Cancel</button>
        </div>
      )}
      {points === null && <p className="text-xs text-ink-tertiary">Loading…</p>}
      {[...byLoc.entries()].map(([loc, list]) => (
        <div key={loc} className="space-y-2">
          <div className="flex items-center gap-1.5 px-1 text-xs font-semibold uppercase tracking-wide text-ink-tertiary"><M n="location_on" c="text-sm" />{loc}</div>
          <div className="rounded-2xl border border-slate-200/90 bg-white shadow-clay-sm divide-y divide-slate-100 overflow-visible">
            {list.map((p) => <PointRow key={p.id} p={p} types={types} canEdit={canEdit} onFlash={flash}
              onSaved={(np) => setPoints((ps) => (ps || []).map((x) => (x.id === np.id ? np : x)))} />)}
          </div>
        </div>
      ))}
      <p className="px-1 text-xs text-ink-tertiary">To ask job-specific questions at check-in, tick "Self check-in" on a field in Settings › Forms.</p>
    </section>
  );
}
