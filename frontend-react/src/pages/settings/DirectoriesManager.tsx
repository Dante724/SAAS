import { useEffect, useMemo, useState } from "react";
import { M } from "../../components/Shell";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog, confirmDialog, promptDialog, selectDialog } from "../../lib/dialog";
import { loadFailed, saveFailed } from "../../lib/notice";
import SettingsPage from "../../components/SettingsPage";
import { sectionCard } from "../../components/Section";
import Button, { IconButton } from "../../components/Button";
import Tabs from "../../components/Tabs";
import EmptyState from "../../components/EmptyState";
import { fieldClass } from "../../components/Field";

// Settings → Directories: the pick-lists behind dropdowns (lead sources, cancellation reasons,
// and any list a custom field is bound to). Lists as cards on the left; the open list shows how
// its options are used (a share strip like the Statuses bar), with rename, merge and hide on
// every row (buttons stay visible for tablets at the counter).
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
    await api.del(`/directories/${d.id}`).catch(saveFailed("delete the list"));
    setSelId(null); loadDirs();
  }

  return (
    <SettingsPage wide title="Directories" sub="The lists your team picks from: where customers came from, why a job was cancelled, and any list a form field uses."
      actions={<Button variant="primary" onClick={newList} icon={<M n="add" c="text-sm" />}>New list</Button>}>
      {/* On phones the lists sit above the open one instead of beside it. */}
      <div className="grid grid-cols-1 md:grid-cols-[240px_minmax(0,1fr)] gap-4 items-start">
        <nav aria-label="Lists" className="space-y-1.5">
          {dirs === null && [0, 1, 2].map((i) => <div key={i} className="sk h-16 rounded-2xl" aria-hidden />)}
          {(dirs || []).map((d) => { const on = d.id === selId; return (
            <button key={d.id} onClick={() => setSelId(d.id)} aria-current={on ? "true" : undefined}
              className={`w-full text-left rounded-2xl border px-3.5 py-3 flex items-center gap-3 transition-colors ${on ? "bg-white border-primary/40 ring-2 ring-primary/10" : "bg-white/70 border-slate-200/90 hover:bg-white hover:border-slate-300"}`}>
              <span className={`w-9 h-9 rounded-xl grid place-items-center flex-shrink-0 ${on ? "bg-primary text-white" : "bg-primary/10 text-primary"}`}><M n={LIST_ICON[d.key] || "format_list_bulleted"} c="text-base" /></span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-ink-primary truncate">{d.name}</span>
                <span className="block text-2xs text-ink-tertiary truncate">{d.item_count} option{d.item_count === 1 ? "" : "s"}{d.used_in.length ? ` · ${d.used_in[0]}` : " · not used yet"}</span>
              </span>
            </button>
          ); })}
          {elsewhere.length > 0 && (
            <div className="rounded-2xl bg-slate-50 px-3.5 py-3 mt-3">
              <div className="text-2xs font-semibold text-ink-tertiary mb-1.5">Set up somewhere else</div>
              {elsewhere.map((e) => (
                <div key={e.name} className="text-2xs text-ink-tertiary leading-snug mb-1"><span className="font-medium text-ink-secondary">{e.name}</span> · {e.where}</div>
              ))}
            </div>
          )}
        </nav>
        {sel ? <ListEditor key={sel.id} dir={sel} onChanged={loadDirs} onDelete={() => delList(sel)} />
          : dirs !== null && <div className={sectionCard}><EmptyState title="No lists yet" body="Lists you make show up here." /></div>}
      </div>
    </SettingsPage>
  );
}

const LIST_ICON: Record<string, string> = { lead_source: "campaign", cancel_reason: "block", problems: "build", problem: "build" };
// Option colours: the same order every time, so an option keeps its colour in the usage strip.
const HUES = ["#378ADD", "#1D9E75", "#7F77DD", "#EF9F27", "#D4537E", "#D85A30", "#639922", "#185FA5", "#888780"];
const USED_FOR: Record<string, string> = { lead_source: "Your team picks one when adding a customer", cancel_reason: "Your team picks one when a job is cancelled" };

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
  const [sortBy, setSortBy] = useState<"mine" | "used">("mine");
  const [hover, setHover] = useState<string | null>(null);
  const flash = (text: string, bad = false) => { setMsg({ text, bad }); setTimeout(() => setMsg(null), 3500); };

  const load = () => api.get<{ items: Item[]; counts_usage?: boolean }>(`/directories/${dir.id}/items?include_archived=true`)
    .then((r) => { setItems(r.items || []); setCounts(!!r.counts_usage); }).catch(() => setItems([]));
  useEffect(() => { load(); api.get<any>("/staff/locations").then((r) => setLocs(r.items || r || [])).catch(loadFailed("locations")); /* eslint-disable-next-line */ }, [dir.id]);

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
    try { await api.del(`/directories/items/${i.id}`); flash(`Hid “${i.value}”. Records that used it keep it`); done(); }
    catch (e: any) {
      if (String(e?.status) === "409" && (await confirmDialog(`“${i.value}” has sub-options. Hide it and move them to the top level?`, { confirmLabel: "Hide" }))) {
        await api.del(`/directories/items/${i.id}?force=true`); done();
      }
    }
  }
  async function restore(i: Item) {
    try { await api.post(`/directories/items/${i.id}/restore`, {}); done(); }
    catch (e: any) { flash(e.message || "Could not restore", true); }
  }

  const total = active.reduce((n, i) => n + (i.uses || 0), 0);
  const colorOf = (id: string) => HUES[Math.max(0, active.findIndex((x) => x.id === id)) % HUES.length];
  const ordered = sortBy === "used" ? [...shown].sort((a, b) => (b.uses || 0) - (a.uses || 0)) : shown;
  const usesText = (n: number) => (n ? `${n} ${noun?.[n === 1 ? 0 : 1] || (n === 1 ? "use" : "uses")}` : "Not used yet");

  return (
    <section className={`${sectionCard} min-w-0`}>
      <div className="px-5 pt-5 pb-4 border-b border-slate-100">
        <div className="flex items-start gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink-primary">{dir.name}</h3>
            <p className="text-xs text-ink-tertiary">{USED_FOR[dir.key] || (dir.used_in.length ? `Used in ${dir.used_in.join(", ")}` : "Not used yet. Bind it to a field in Forms & fields.")}</p>
          </div>
          <div className="ml-auto flex items-center gap-1 flex-shrink-0">
            <IconButton label="Download as a spreadsheet" onClick={() => openAuthedDoc(`/directories/${dir.id}/export`).catch(() => flash("Export not available", true))} icon={<M n="download" c="text-base" />} />
            {!dir.is_system && <IconButton label="Delete list" onClick={onDelete} icon={<M n="delete" c="text-base" />} className="hover:!text-rose-600" />}
          </div>
        </div>
        {counts && total > 0 && (
          <div className="mt-4">
            <div className="flex h-2.5 rounded-full overflow-hidden bg-slate-100">
              {active.filter((i) => i.uses).map((i) => (
                <div key={i.id} title={`${i.value}: ${usesText(i.uses)}`} className={`transition-opacity ${hover && hover !== i.id ? "opacity-30" : ""}`} style={{ width: `${(i.uses / total) * 100}%`, background: colorOf(i.id) }} />
              ))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-2xs text-ink-secondary">
              {[...active].filter((i) => i.uses).sort((a, b) => b.uses - a.uses).slice(0, 4).map((i) => (
                <span key={i.id} className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: colorOf(i.id) }} />{i.value} {Math.round((i.uses / total) * 100)}%</span>
              ))}
              <span className="text-ink-tertiary">of {total} {noun?.[total === 1 ? 0 : 1] || "uses"}</span>
            </div>
          </div>
        )}
        {dir.used_in.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 mt-3">
            {dir.used_in.map((u) => <span key={u} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 text-ink-secondary text-2xs"><M n="link" c="text-xs" />{u}</span>)}
          </div>
        )}
      </div>

      <div className="p-5 space-y-3">
        <div className="flex gap-2">
          <input value={nv} onChange={(e) => setNv(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }} placeholder="Add an option and press Enter" aria-label="New option"
            className={fieldClass("flex-1 min-w-0")} />
          <Button variant="primary" onClick={add} disabled={!nv.trim()}>Add</Button>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="link" onClick={() => setPaste(paste === null ? "" : null)}>Paste a list</Button>
          <Button variant="link" onClick={() => setMore((m) => !m)} className="!text-ink-secondary">{more ? "Hide extra options" : "Sub-option or one location only"}</Button>
          {msg && <span role="status" className={`text-xs font-semibold ${msg.bad ? "text-rose-600" : "text-emerald-700"}`}>{msg.text}</span>}
          {/* Stays in place (greyed) with one option, so the row never changes. */}
          <Tabs look="segmented" label="Sort options" value={sortBy} onChange={setSortBy} className="ml-auto"
            tabs={[{ id: "mine", label: "Your order", disabled: active.length < 2 }, { id: "used", label: "Most used", disabled: active.length < 2 }]} />
        </div>
        {more && (
          <div className="flex flex-wrap gap-2">
            <select value={np} onChange={(e) => setNp(e.target.value)} aria-label="Put it under" className={fieldClass("!w-auto pr-8")}><option value="">Top level</option>{active.map((i) => <option key={i.id} value={i.id}>Under {i.value}</option>)}</select>
            <select value={nl} onChange={(e) => setNl(e.target.value)} aria-label="Location" className={fieldClass("!w-auto pr-8")}><option value="">All locations</option>{locs.map((l) => <option key={l.id} value={l.id}>{l.name} only</option>)}</select>
          </div>
        )}
        {paste !== null && (
          <div className="space-y-1.5">
            <textarea autoFocus value={paste} onChange={(e) => setPaste(e.target.value)} placeholder={"One option per line\nNextdoor\nRadio ad"} className={fieldClass("h-28 py-2")} aria-label="Options to add, one per line" />
            <Button variant="primary" onClick={addPasted} disabled={!paste.trim()}>Add {paste.split(/\r?\n/).filter((l) => l.trim()).length || ""} options</Button>
          </div>
        )}
        {active.length > 8 && (
          <div className="relative"><M n="search" c="absolute left-3 top-2.5 text-ink-tertiary text-base" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an option" aria-label="Find an option" className={fieldClass("pl-9")} /></div>
        )}

        <div className="rounded-xl border border-slate-200/80 divide-y divide-slate-100 overflow-hidden">
          {items === null && <div className="px-4 py-3" aria-busy="true"><span className="sk block h-3 w-1/2 rounded" /></div>}
          {items && ordered.length === 0 && <EmptyState size="inline" className="px-4" title={q ? "No matching options." : "No options yet. Add the first one above."} />}
          {ordered.map((i, idx) => (
            // On phones the counts and buttons wrap under the name, so the name keeps its full width.
            <div key={i.id} onMouseEnter={() => setHover(i.id)} onMouseLeave={() => setHover(null)} className="px-3 py-1.5 min-h-[48px] flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm hover:bg-slate-50/70">
              {!q && sortBy === "mine" && <div className="flex items-center">
                <IconButton size="sm" label={`Move ${i.value} up`} disabled={idx === 0} onClick={() => move(i, -1)} icon={<M n="expand_less" c="text-sm" />} className="!w-7 disabled:opacity-20" />
                <IconButton size="sm" label={`Move ${i.value} down`} disabled={idx === ordered.length - 1} onClick={() => move(i, 1)} icon={<M n="expand_more" c="text-sm" />} className="!w-7 disabled:opacity-20" />
              </div>}
              <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: colorOf(i.id) }} />
              {editId === i.id ? (
                <input autoFocus value={editVal} onChange={(e) => setEditVal(e.target.value)} onBlur={() => saveRename(i)} aria-label="Rename option"
                  onKeyDown={(e) => { if (e.key === "Enter") saveRename(i); if (e.key === "Escape") setEditId(null); }}
                  className={fieldClass("flex-1 basis-40 h-8 min-w-0")} />
              ) : (
                <button type="button" onClick={() => { setEditId(i.id); setEditVal(i.value); }} title="Click to rename" className="flex-1 basis-40 min-w-0 text-left text-ink-primary truncate">{i.value}</button>
              )}
              <div className={`flex flex-wrap items-center gap-2 max-sm:w-full ${!q && sortBy === "mine" ? "max-sm:pl-[5.25rem]" : "max-sm:pl-5"}`}>
                {i.parent_id && <span className="text-2xs text-ink-tertiary">under {byId(i.parent_id)?.value || "—"}</span>}
                {i.location_id && <span className="px-1.5 py-0.5 rounded-md bg-primary/10 text-primary text-2xs font-semibold">{locs.find((l) => l.id === i.location_id)?.name || "1 location"}</span>}
                {counts && <span className={`text-2xs tabular-nums whitespace-nowrap ${i.uses ? "text-ink-secondary" : "text-ink-tertiary"}`}>{usesText(i.uses)}</span>}
                <Button variant="ghost" size="sm" onClick={() => mergeInto(i)}>Merge</Button>
                <Button variant="ghost" size="sm" onClick={() => archive(i)} className="hover:!text-rose-600">Hide</Button>
              </div>
            </div>
          ))}
        </div>

        {archived.length > 0 && (
          <div className="pt-1">
            <Button variant="link" onClick={() => setShowArchived((s) => !s)} aria-expanded={showArchived} className="!text-ink-secondary">
              <M n={showArchived ? "expand_less" : "expand_more"} c="text-sm" />Hidden ({archived.length})</Button>
            {showArchived && (
              <div className="mt-2 rounded-xl bg-slate-50 divide-y divide-slate-100">
                <div className="px-3 py-2 text-2xs text-ink-tertiary">Jobs and customers that used these keep them. New ones can't pick them.</div>
                {archived.map((i) => (
                  <div key={i.id} className="flex items-center gap-2 px-3 py-2 text-sm text-ink-tertiary">
                    <span className="flex-1 min-w-0 truncate">{i.value}</span>
                    <Button variant="link" onClick={() => restore(i)}>Show again</Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
