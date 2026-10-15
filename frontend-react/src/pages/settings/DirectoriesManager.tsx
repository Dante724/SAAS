import { useEffect, useMemo, useState } from "react";
import { M } from "../../components/Shell";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog, confirmDialog, promptDialog, selectDialog } from "../../lib/dialog";

// Settings → Directories: the pick-lists behind dropdowns (lead sources, cancellation reasons,
// and any list a custom field is bound to). Split view: lists on the left, the selected list's
// options on the right. Every list says where it's used, so editing it is never a mystery.
type Dir = { id: string; key: string; name: string; is_system: boolean; item_count: number; used_in: string[] };
type Item = { id: string; value: string; parent_id?: string | null; location_id?: string | null; active: boolean; child_count: number; uses: number };

// What a usage count is counting, per built-in list.
const USES_NOUN: Record<string, [string, string]> = { lead_source: ["client", "clients"], cancel_reason: ["job", "jobs"] };

const slug = (s: string) => s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40);

export default function DirectoriesManager() {
  const [dirs, setDirs] = useState<Dir[] | null>(null);
  const [elsewhere, setElsewhere] = useState<{ name: string; where: string }[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const loadDirs = () => api.get<{ items: Dir[]; managed_elsewhere?: any[] }>("/directories").then((r) => {
    setDirs(r.items || []); setElsewhere(r.managed_elsewhere || []);
    setSelId((cur) => {
      if (cur && (r.items || []).some((d) => d.id === cur)) return cur;
      const want = new URLSearchParams(window.location.search).get("dir");
      return (r.items || []).find((d) => d.key === want)?.id || r.items?.[0]?.id || null;
    });
  }).catch(() => setDirs([]));
  useEffect(() => { loadDirs(); }, []);
  const sel = (dirs || []).find((d) => d.id === selId) || null;

  async function newList() {
    const name = (await promptDialog("Name the new list", { placeholder: "e.g. Device condition", confirmLabel: "Create" }))?.trim();
    if (!name) return;
    try {
      const r = await api.post<Dir>("/directories", { name, key: slug(name) || `list_${Date.now()}` });
      await loadDirs(); setSelId(r.id);
    } catch (e: any) { await alertDialog(e.message || "Could not create the list"); }
  }
  async function delList(d: Dir) {
    if (!(await confirmDialog(`Delete “${d.name}” and its ${d.item_count} option(s)? Custom fields using it will show no options.`, { danger: true, confirmLabel: "Delete list" }))) return;
    await api.del(`/directories/${d.id}`).catch(() => {});
    setSelId(null); loadDirs();
  }

  return (
    <div className="max-w-5xl grid grid-cols-1 md:grid-cols-[260px_1fr] gap-5 items-start">
      {/* Lists */}
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-[11px] font-bold uppercase tracking-wider text-ink-tertiary">Lists</span>
          <button onClick={newList} className="text-xs font-semibold text-primary flex items-center gap-1"><M n="add" c="text-sm" />New list</button>
        </div>
        {dirs === null && <div className="px-4 py-3 text-xs text-ink-tertiary">Loading…</div>}
        {(dirs || []).map((d) => (
          <button key={d.id} onClick={() => setSelId(d.id)}
            className={`w-full text-left px-4 py-2.5 flex items-center gap-2 border-l-2 ${d.id === selId ? "bg-blue-50/60 border-primary" : "border-transparent hover:bg-slate-50"}`}>
            <span className={`flex-1 text-sm ${d.id === selId ? "font-bold text-ink-primary" : "font-semibold text-ink-secondary"}`}>{d.name}</span>
            <span className="text-[11px] font-semibold text-ink-tertiary">{d.item_count}</span>
          </button>
        ))}
        {elsewhere.length > 0 && (
          <div className="px-4 py-3 border-t border-slate-100 space-y-1.5">
            <div className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary">Managed elsewhere</div>
            {elsewhere.map((e) => (
              <div key={e.name} className="text-[11px] text-ink-tertiary leading-snug"><span className="font-semibold text-ink-secondary">{e.name}</span> — {e.where}</div>
            ))}
          </div>
        )}
      </div>

      {/* Options of the selected list */}
      {sel ? <ListEditor key={sel.id} dir={sel} onChanged={loadDirs} onDelete={() => delList(sel)} />
        : <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-6 text-sm text-ink-tertiary">Create a list to get started.</div>}
    </div>
  );
}

function ListEditor({ dir, onChanged, onDelete }: { dir: Dir; onChanged: () => void; onDelete: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [counts, setCounts] = useState(false);
  const [locs, setLocs] = useState<{ id: string; name: string }[]>([]);
  const [q, setQ] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [nv, setNv] = useState("");
  const [more, setMore] = useState(false); const [np, setNp] = useState(""); const [nl, setNl] = useState("");
  const [paste, setPaste] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null); const [editVal, setEditVal] = useState("");
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const flash = (text: string, bad = false) => { setMsg({ text, bad }); setTimeout(() => setMsg(null), 3500); };

  const load = () => api.get<{ items: Item[]; counts_usage?: boolean }>(`/directories/${dir.id}/items?include_archived=true`)
    .then((r) => { setItems(r.items || []); setCounts(!!r.counts_usage); }).catch(() => setItems([]));
  useEffect(() => { load(); api.get<any>("/staff/locations").then((r) => setLocs(r.items || r || [])).catch(() => {}); /* eslint-disable-next-line */ }, [dir.id]);

  const active = useMemo(() => (items || []).filter((i) => i.active), [items]);
  const archived = useMemo(() => (items || []).filter((i) => !i.active), [items]);
  const shown = active.filter((i) => !q || i.value.toLowerCase().includes(q.toLowerCase()));
  const byId = (id?: string | null) => (items || []).find((x) => x.id === id);
  const noun = USES_NOUN[dir.key];
  const done = () => { load(); onChanged(); };

  async function add() {
    const v = nv.trim(); if (!v) return;
    try { await api.post(`/directories/${dir.id}/items`, { value: v, parent_id: np || null, location_id: nl || null }); setNv(""); done(); }
    catch (e: any) { flash(e.message || "Could not add", true); }
  }
  async function addPasted() {
    const lines = (paste || "").split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (!lines.length) return;
    const csv = lines.map((l) => `"${l.replace(/"/g, '""')}"`).join("\n");
    try { const r = await api.post<any>(`/directories/${dir.id}/import`, { csv }); setPaste(null); flash(`Added ${r.added ?? lines.length}`); done(); }
    catch (e: any) { flash(e.message || "Could not add", true); }
  }
  async function saveRename(i: Item) {
    const v = editVal.trim(); setEditId(null);
    if (!v || v === i.value) return;
    try {
      const r = await api.patch<any>(`/directories/items/${i.id}`, { value: v });
      flash(r.records_updated ? `Renamed — ${r.records_updated} record(s) updated` : "Renamed"); done();
    } catch (e: any) { flash(e.message || "Could not rename", true); }
  }
  async function move(i: Item, dirn: -1 | 1) {
    const ids = active.map((x) => x.id); const at = ids.indexOf(i.id); const to = at + dirn;
    if (to < 0 || to >= ids.length) return;
    [ids[at], ids[to]] = [ids[to], ids[at]];
    setItems((cur) => { const a = [...(cur || [])]; const ai = a.findIndex((x) => x.id === ids[at]); const bi = a.findIndex((x) => x.id === ids[to]); [a[ai], a[bi]] = [a[bi], a[ai]]; return a; });
    await api.post(`/directories/${dir.id}/reorder`, { ids }).catch(() => load());
  }
  async function mergeInto(i: Item) {
    const others = active.filter((x) => x.id !== i.id).map((x) => x.value);
    const target = await selectDialog(`Merge “${i.value}” into…${counts && i.uses ? `\nIts ${i.uses} ${noun?.[i.uses === 1 ? 0 : 1] || "record(s)"} move to the option you pick.` : ""}`, others, { title: "Merge option", confirmLabel: "Merge" });
    if (!target) return;
    const t = active.find((x) => x.value === target); if (!t) return;
    try { const r = await api.post<any>(`/directories/items/${i.id}/merge`, { target_id: t.id }); flash(`Merged into ${r.kept}${r.records_updated ? ` — ${r.records_updated} record(s) updated` : ""}`); done(); }
    catch (e: any) { flash(e.message || "Could not merge", true); }
  }
  async function archive(i: Item) {
    try { await api.del(`/directories/items/${i.id}`); flash(`Archived “${i.value}” — existing records keep it`); done(); }
    catch (e: any) {
      if (String(e?.status) === "409" && (await confirmDialog(`“${i.value}” has sub-options. Archive it and move them to the top level?`, { confirmLabel: "Archive" }))) {
        await api.del(`/directories/items/${i.id}?force=true`); done();
      }
    }
  }
  async function restore(i: Item) {
    try { await api.post(`/directories/items/${i.id}/restore`, {}); done(); }
    catch (e: any) { flash(e.message || "Could not restore", true); }
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm">
      <div className="px-5 pt-4 pb-3 border-b border-slate-100 space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-bold text-ink-primary">{dir.name}</h3>
            <p className="text-xs text-ink-tertiary mt-0.5">{active.length} option{active.length === 1 ? "" : "s"}{archived.length ? ` · ${archived.length} archived` : ""}</p>
          </div>
          <div className="flex items-center gap-1.5">
            <button onClick={() => openAuthedDoc(`/directories/${dir.id}/export`).catch(() => flash("Export not available", true))} title="Export CSV" className="p-1.5 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="download" c="text-base" /></button>
            {!dir.is_system && <button onClick={onDelete} title="Delete list" className="p-1.5 rounded-lg text-ink-tertiary hover:text-rose-600 hover:bg-rose-50"><M n="delete" c="text-base" /></button>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold text-ink-tertiary">Used in:</span>
          {dir.used_in.length ? dir.used_in.map((u) => <span key={u} className="px-2 py-0.5 rounded-full bg-slate-100 text-ink-secondary text-[11px] font-semibold">{u}</span>)
            : <span className="text-[11px] text-amber-700">Nothing yet — bind it to a custom field in Forms & fields.</span>}
        </div>
      </div>

      <div className="p-5 space-y-3">
        {active.length > 8 && (
          <div className="relative"><M n="search" c="absolute left-3 top-2.5 text-ink-tertiary text-base" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search options" className="glass-input w-full pl-9 pr-3 py-2 rounded-lg text-sm" /></div>
        )}

        <div className="border border-slate-200/80 rounded-xl divide-y divide-slate-100 overflow-hidden">
          {items === null && <div className="px-4 py-3 text-sm text-ink-tertiary">Loading…</div>}
          {items && shown.length === 0 && <div className="px-4 py-3 text-sm text-ink-tertiary">{q ? "No matching options." : "No options yet — add the first one below."}</div>}
          {shown.map((i, idx) => (
            <div key={i.id} className="group px-3 py-2 flex items-center gap-2 text-sm hover:bg-slate-50/70">
              {!q && <div className="flex flex-col -my-1">
                <button aria-label="Move up" disabled={idx === 0} onClick={() => move(i, -1)} className="text-ink-tertiary hover:text-primary disabled:opacity-20 leading-none"><M n="expand_less" c="text-sm" /></button>
                <button aria-label="Move down" disabled={idx === shown.length - 1} onClick={() => move(i, 1)} className="text-ink-tertiary hover:text-primary disabled:opacity-20 leading-none"><M n="expand_more" c="text-sm" /></button>
              </div>}
              {editId === i.id ? (
                <input autoFocus value={editVal} onChange={(e) => setEditVal(e.target.value)} onBlur={() => saveRename(i)}
                  onKeyDown={(e) => { if (e.key === "Enter") saveRename(i); if (e.key === "Escape") setEditId(null); }}
                  className="glass-input flex-1 px-2 py-1 rounded-lg text-sm font-semibold" />
              ) : (
                <button onClick={() => { setEditId(i.id); setEditVal(i.value); }} title="Click to rename" className="flex-1 text-left font-semibold text-ink-primary truncate">{i.value}</button>
              )}
              {i.parent_id && <span className="text-[11px] text-ink-tertiary">under {byId(i.parent_id)?.value || "—"}</span>}
              {i.location_id && <span className="px-1.5 py-0.5 rounded bg-blue-50 text-primary text-[10px] font-semibold">{locs.find((l) => l.id === i.location_id)?.name || "1 location"}</span>}
              {counts && <span className="text-[11px] text-ink-tertiary tabular-nums w-20 text-right">{i.uses ? `${i.uses} ${noun?.[i.uses === 1 ? 0 : 1] || "uses"}` : "unused"}</span>}
              <div className="flex items-center gap-0.5 opacity-60 group-hover:opacity-100">
                <button aria-label="Merge into another option" title="Merge into…" onClick={() => mergeInto(i)} className="p-1 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="merge" c="text-base" /></button>
                <button aria-label="Archive" title="Archive" onClick={() => archive(i)} className="p-1 rounded-lg text-ink-tertiary hover:text-rose-600 hover:bg-rose-50"><M n="archive" c="text-base" /></button>
              </div>
            </div>
          ))}
        </div>

        {/* Add */}
        <div className="space-y-2">
          <div className="flex gap-2">
            <input value={nv} onChange={(e) => setNv(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }} placeholder="Add an option and press Enter"
              className="glass-input flex-1 px-3 py-2 rounded-lg text-sm" />
            <button onClick={add} disabled={!nv.trim()} className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-semibold hover:bg-primary-container disabled:opacity-50">Add</button>
          </div>
          <div className="flex items-center gap-3 text-[11px] font-semibold">
            <button onClick={() => setPaste(paste === null ? "" : null)} className="text-primary hover:underline">Paste a list</button>
            <button onClick={() => setMore((m) => !m)} className="text-ink-tertiary hover:text-ink-secondary">{more ? "Hide" : "Sub-option or location…"}</button>
            {msg && <span className={msg.bad ? "text-rose-600" : "text-emerald-700"}>{msg.text}</span>}
          </div>
          {more && (
            <div className="flex flex-wrap gap-2">
              <select value={np} onChange={(e) => setNp(e.target.value)} className="glass-input px-2 py-2 rounded-lg text-xs"><option value="">Top level</option>{active.map((i) => <option key={i.id} value={i.id}>Under {i.value}</option>)}</select>
              <select value={nl} onChange={(e) => setNl(e.target.value)} className="glass-input px-2 py-2 rounded-lg text-xs"><option value="">All locations</option>{locs.map((l) => <option key={l.id} value={l.id}>{l.name} only</option>)}</select>
            </div>
          )}
          {paste !== null && (
            <div className="space-y-1.5">
              <textarea autoFocus value={paste} onChange={(e) => setPaste(e.target.value)} placeholder={"One option per line\nNextdoor\nRadio ad"} className="glass-input w-full h-28 px-3 py-2 rounded-lg text-sm" />
              <button onClick={addPasted} disabled={!paste.trim()} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-50">Add {paste.split(/\r?\n/).filter((l) => l.trim()).length || ""} options</button>
            </div>
          )}
        </div>

        {archived.length > 0 && (
          <div className="pt-2 border-t border-slate-100">
            <button onClick={() => setShowArchived((s) => !s)} className="text-[11px] font-semibold text-ink-tertiary hover:text-ink-secondary flex items-center gap-1">
              <M n={showArchived ? "expand_less" : "expand_more"} c="text-sm" />Archived ({archived.length})</button>
            {showArchived && <div className="mt-2 space-y-1">{archived.map((i) => (
              <div key={i.id} className="flex items-center gap-2 text-sm text-ink-tertiary px-1">
                <span className="flex-1 line-through">{i.value}</span>
                <button onClick={() => restore(i)} className="text-[11px] font-semibold text-primary hover:underline">Restore</button>
              </div>
            ))}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
