import { useEffect, useState } from "react";
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
  ["auto", "Automatic (follows opening hours)"], ["walk_in", "Walk-in queue"], ["key_drop", "Key drop only"],
];

function PointCard({ p, types, canEdit, onSaved, onFlash }: { p: Point; types: JobType[]; canEdit: boolean; onSaved: (p: Point) => void; onFlash: (m: string) => void }) {
  const [f, setF] = useState<Point>(p);
  const [busy, setBusy] = useState(false);
  useEffect(() => setF(p), [p]);
  const dirty = JSON.stringify(f) !== JSON.stringify(p);
  const set = (patch: Partial<Point>) => setF((x) => ({ ...x, ...patch }));

  async function run(fn: () => Promise<Point>, msg: string) {
    setBusy(true);
    try { onSaved(await fn()); onFlash(msg); } catch (e: any) { onFlash(e?.message || "Couldn't save"); } finally { setBusy(false); }
  }
  const save = () => run(() => api.put<Point>(`/checkins/points/${p.id}`, f), "Check-in point saved");
  async function rotate() {
    if (!(await confirmDialog("The current QR stops working straight away. Print the new poster and replace the old one. People already in the queue keep their links.", { title: "Replace this QR code?", confirmLabel: "Replace code" }))) return;
    run(() => api.post<Point>(`/checkins/points/${p.id}/rotate`, {}), "New code ready. Print the new poster.");
  }
  async function poster() {
    try { const r = await api.get<{ html: string }>(`/checkins/points/${p.id}/poster`); printHtmlInFrame(r.html); }
    catch (e: any) { onFlash(e?.message || "Couldn't prepare the poster"); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(p.url || window.location.origin + p.path); onFlash("Link copied"); } catch { onFlash(p.url || p.path); }
  }
  const inp = "glass-input w-full px-3 py-2 rounded-lg text-xs";

  return (
    <div className={`rounded-2xl border border-slate-200/90 bg-white shadow-clay-card p-4 space-y-3 text-xs ${f.active ? "" : "opacity-70"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="Point name" value={f.name} disabled={!canEdit} onChange={(e) => set({ name: e.target.value })} className={`${inp} max-w-[220px] font-semibold`} />
        <span className="text-[11px] text-ink-tertiary">{p.scans} scans · {p.submits} check-ins</span>
        <div className="ml-auto flex flex-wrap gap-1.5">
          <button onClick={poster} className="h-8 px-3 rounded-lg border border-slate-200 font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="print" c="text-sm" />Print poster</button>
          <button onClick={() => openAuthedDoc(`/api/checkins/points/${p.id}/qr.svg`).catch(() => onFlash("Couldn't download the QR"))} className="h-8 px-3 rounded-lg border border-slate-200 font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="qr_code_2" c="text-sm" />Download QR</button>
          <button onClick={copy} className="h-8 px-3 rounded-lg border border-slate-200 font-semibold text-ink-secondary hover:bg-slate-50">Copy link</button>
          <a href={p.path} target="_blank" rel="noopener noreferrer" className="h-8 px-3 rounded-lg border border-slate-200 font-semibold text-ink-secondary hover:bg-slate-50 flex items-center">Preview</a>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`mode-${p.id}`} className="block font-semibold text-ink-secondary mb-1">How it works</label>
          <select id={`mode-${p.id}`} value={f.mode} disabled={!canEdit} onChange={(e) => set({ mode: e.target.value as Point["mode"] })} className={inp}>
            {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {f.mode === "auto" && <p className="mt-1 text-[11px] text-ink-tertiary">Walk-in queue while open, key drop when closed.</p>}
        </div>
        <div>
          <div className="block font-semibold text-ink-secondary mb-1">Job types offered</div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {types.length === 0 && <span className="text-ink-tertiary">Add job types in Settings › Forms.</span>}
            {types.map((t) => (
              <label key={t.id} className="flex items-center gap-1.5">
                <input type="checkbox" disabled={!canEdit} checked={f.job_type_ids.includes(t.id)}
                  onChange={(e) => set({ job_type_ids: e.target.checked ? [...f.job_type_ids, t.id] : f.job_type_ids.filter((x) => x !== t.id) })} />{t.name}
              </label>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-ink-tertiary">None ticked uses the default job type.</p>
        </div>
      </div>
      <div>
        <label htmlFor={`welcome-${p.id}`} className="block font-semibold text-ink-secondary mb-1">Welcome text (optional)</label>
        <input id={`welcome-${p.id}`} value={f.welcome_text} disabled={!canEdit} onChange={(e) => set({ welcome_text: e.target.value })} placeholder="Shown under the heading" className={inp} />
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1.5">
        {/* Only the options this mode can use: a key-drop-only point has no queue, a walk-in-only point no key drops. */}
        {f.mode !== "key_drop" && <label className="flex items-center gap-1.5"><input type="checkbox" disabled={!canEdit} checked={f.kiosk} onChange={(e) => set({ kiosk: e.target.checked })} />Counter tablet (clears after each customer; staff sign-in to exit)</label>}
        {f.mode !== "key_drop" && <label className="flex items-center gap-1.5"><input type="checkbox" disabled={!canEdit} checked={f.show_wait} onChange={(e) => set({ show_wait: e.target.checked })} />Show estimated wait</label>}
        {f.mode !== "walk_in" && <label className="flex items-center gap-1.5"><input type="checkbox" disabled={!canEdit} checked={f.require_photo} onChange={(e) => set({ require_photo: e.target.checked })} />Key drop needs a dashboard photo</label>}
        <label className="flex items-center gap-1.5"><input type="checkbox" disabled={!canEdit} checked={f.active} onChange={(e) => set({ active: e.target.checked })} />Active</label>
      </div>
      {canEdit && (
        <div className="flex items-center gap-2">
          <button onClick={save} disabled={!dirty || busy} className="h-8 px-4 rounded-lg bg-primary text-white font-bold disabled:opacity-40">Save</button>
          <button onClick={rotate} disabled={busy} className="h-8 px-3 rounded-lg text-rose-600 font-semibold hover:bg-rose-50">Replace QR code</button>
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
          <p className="text-xs text-ink-tertiary">Customers scan a QR at the door, key box or counter tablet and check themselves in. Each point has its own code. Check-ins appear in Inquiries › Walk-ins. To ask job-specific questions, tick "Self check-in" on a field in Settings › Forms.</p>
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
          <div className="text-sm font-semibold text-ink-primary flex items-center gap-1.5"><M n="location_on" c="text-base text-ink-tertiary" />{loc}</div>
          {list.map((p) => <PointCard key={p.id} p={p} types={types} canEdit={canEdit} onFlash={flash}
            onSaved={(np) => setPoints((ps) => (ps || []).map((x) => (x.id === np.id ? np : x)))} />)}
        </div>
      ))}
    </section>
  );
}
