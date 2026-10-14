import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { confirmDialog, promptDialog } from "../lib/dialog";

// Category tree manager (add / rename / move / delete) on existing /catalog/categories.
// entity_type scopes the tree (product|service|bundle). auto_deduct applies to bundles,
// template_key to industry-scoped trees. ponytail: auto_deduct is create-only (backend
// PATCH has no auto_deduct field); rename/move/template edit via PATCH.
type Node = { id: string; name: string; auto_deduct?: boolean; template_key?: string | null; own_count?: number; count?: number; children?: Node[]; __parentId?: string };

export default function CategoryManager({ entity, onClose, onChanged }: {
  entity: "product" | "service" | "bundle"; onClose: () => void; onChanged?: () => void;
}) {
  const [tree, setTree] = useState<Node[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [nf, setNf] = useState({ name: "", parent_id: "", auto_deduct: false });

  const load = () => api.get<{ tree: Node[] }>(`/catalog/categories?entity_type=${entity}`).then((r) => setTree(r.tree || [])).catch(() => setTree([]));
  useEffect(() => { load(); }, [entity]);

  // flatten to [{node, depth}] for indented rows + a move-target list
  const flat: { n: Node; depth: number }[] = [];
  const walk = (ns: Node[], d: number, parent?: string) => ns.forEach((n) => { n.__parentId = parent; flat.push({ n, depth: d }); if (n.children?.length) walk(n.children, d + 1, n.id); });
  if (tree) walk(tree, 0);

  async function add() {
    if (!nf.name.trim()) return;
    setErr(null);
    try {
      await api.post("/catalog/categories", { entity_type: entity, name: nf.name.trim(), parent_id: nf.parent_id || null, auto_deduct: entity === "bundle" ? nf.auto_deduct : false });
      setNf({ name: "", parent_id: "", auto_deduct: false }); load(); onChanged?.();
    } catch (e: any) { setErr(e.message || "Could not add"); }
  }
  async function rename(n: Node) {
    const name = await promptDialog("Rename category", { defaultValue: n.name }); if (!name || name === n.name) return;
    try { await api.patch(`/catalog/categories/${n.id}`, { name: name.trim() }); load(); onChanged?.(); } catch (e: any) { setErr(e.message || "Rename failed"); }
  }
  async function move(id: string, parent_id: string) {
    try { await api.patch(`/catalog/categories/${id}`, { parent_id: parent_id || null }); load(); onChanged?.(); } catch (e: any) { setErr(e.message || "Move failed"); }
  }
  async function del(n: Node) {
    if (!(await confirmDialog(`Delete "${n.name}"?`, { danger: true, confirmLabel: "Delete" }))) return;
    try { await api.del(`/catalog/categories/${n.id}`); load(); onChanged?.(); } catch (e: any) { setErr(e.message || "In use — move its items/sub-categories first"); }
  }

  return (
    <div className="fixed inset-0 z-[60] flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[460px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary capitalize">{entity} categories</h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-1">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2 mb-2">{err}</div>}
          {tree == null && <div className="text-xs text-ink-tertiary">Loading…</div>}
          {tree && flat.length === 0 && <div className="text-xs text-ink-tertiary">No categories yet — add one below.</div>}
          {flat.map(({ n, depth }) => (
            <div key={n.id} className="flex items-center gap-2 py-1.5 border-b border-slate-50" style={{ paddingLeft: depth * 16 }}>
              <M n="chevron_right" c="text-xs text-ink-tertiary" />
              <span className="flex-1 text-sm font-semibold text-ink-primary truncate">{n.name}{typeof n.count === "number" && <span className="ml-1.5 text-[10px] text-ink-tertiary font-normal">({n.count})</span>}{entity === "bundle" && n.auto_deduct && <span className="ml-1.5 text-[9px] px-1 rounded bg-amber-100 text-amber-700 font-bold uppercase">auto-deduct</span>}</span>
              <select value={n.__parentId || ""} onChange={(e) => move(n.id, e.target.value)} title="Move under…" className="text-[11px] border border-slate-200 rounded-md px-1 py-0.5 bg-white max-w-[110px]">
                <option value="">Top level</option>
                {flat.filter((x) => x.n.id !== n.id).map((x) => <option key={x.n.id} value={x.n.id}>{"— ".repeat(x.depth)}{x.n.name}</option>)}
              </select>
              <button aria-label="Rename" onClick={() => rename(n)} className="text-ink-tertiary hover:text-primary" title="Rename"><M n="description" c="text-xs" /></button>
              <button aria-label="Delete" onClick={() => del(n)} className="text-ink-tertiary hover:text-rose-600" title="Delete"><M n="delete" c="text-xs" /></button>
            </div>
          ))}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 space-y-2">
          <div className="text-[11px] font-semibold text-ink-tertiary uppercase tracking-wide">Add category</div>
          <div className="flex items-center gap-2">
            <input value={nf.name} onChange={(e) => setNf({ ...nf, name: e.target.value })} placeholder="Name" className="glass-input flex-1 px-3 py-2 rounded-lg text-xs" />
            <select value={nf.parent_id} onChange={(e) => setNf({ ...nf, parent_id: e.target.value })} className="text-xs border border-slate-200 rounded-lg px-2 py-2 bg-white max-w-[130px]">
              <option value="">Top level</option>
              {flat.map((x) => <option key={x.n.id} value={x.n.id}>{"— ".repeat(x.depth)}{x.n.name}</option>)}
            </select>
            <button onClick={add} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container">Add</button>
          </div>
          {entity === "bundle" && <label className="flex items-center gap-2 text-xs font-medium text-ink-secondary"><input type="checkbox" checked={nf.auto_deduct} onChange={(e) => setNf({ ...nf, auto_deduct: e.target.checked })} className="accent-primary" />Auto-deduct component stock (set on create)</label>}
        </div>
      </div>
    </div>
  );
}
