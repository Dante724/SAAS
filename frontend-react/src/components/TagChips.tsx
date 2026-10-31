import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { loadFailed } from "../lib/notice";

export type Tag = { id: string; name: string; color: string };

/** Reusable tag strip: shows an entity's tags, add existing / create new / remove.
 *  Backend: /api/tags (+ /attach, /detach, /for). entityType in contact|order|chat. */
export default function TagChips({ entityType, entityId, className = "" }:
  { entityType: "contact" | "order" | "chat"; entityId: string; className?: string }) {
  const [mine, setMine] = useState<Tag[]>([]);
  const [all, setAll] = useState<Tag[]>([]);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);

  const load = () => {
    api.get<{ items: Tag[] }>(`/tags/for?entity_type=${entityType}&entity_id=${entityId}`)
      .then((r) => setMine(r.items || [])).catch(() => setMine([]));
  };
  useEffect(load, [entityType, entityId]);
  useEffect(() => { api.get<{ items: Tag[] }>("/tags").then((r) => setAll(r.items || [])).catch(loadFailed("tags")); }, []);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, []);

  const attach = async (tagId: string) => {
    await api.post("/tags/attach", { tag_id: tagId, entity_type: entityType, entity_id: entityId });
    setOpen(false); setQ(""); load();
  };
  const detach = async (tagId: string) => {
    await api.post("/tags/detach", { tag_id: tagId, entity_type: entityType, entity_id: entityId }); load();
  };
  const createAndAttach = async () => {
    const name = q.trim(); if (!name) return;
    try {
      const t = await api.post<Tag>("/tags", { name });
      setAll((a) => [...a, t]); await attach(t.id);
    } catch { /* likely duplicate — try attaching the existing one */
      const ex = all.find((t) => t.name.toLowerCase() === name.toLowerCase());
      if (ex) await attach(ex.id);
    }
  };

  const mineIds = new Set(mine.map((t) => t.id));
  const candidates = all.filter((t) => !mineIds.has(t.id) &&
    (!q || t.name.toLowerCase().includes(q.toLowerCase())));

  return (
    <div ref={box} className={`relative flex flex-wrap items-center gap-1.5 ${className}`}>
      {mine.map((t) => (
        <span key={t.id} className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full text-[11px] font-bold text-white"
              style={{ backgroundColor: t.color || "#64748b" }}>
          {t.name}
          <button onClick={() => detach(t.id)} className="w-4 h-4 rounded-full grid place-items-center hover:bg-black/20" title="Remove">×</button>
        </span>
      ))}
      <button onClick={() => setOpen((v) => !v)}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold text-ink-tertiary border border-dashed border-slate-300 hover:border-primary hover:text-primary">
        + Tag
      </button>
      {open && (
        <div className="absolute z-30 top-7 left-0 w-56 bg-white rounded-xl border border-slate-200 shadow-clay-card p-2">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && !candidates.length) createAndAttach(); }}
                 placeholder="Find or create a tag…"
                 className="glass-input w-full py-1.5 px-2 text-xs rounded-lg mb-1.5" />
          <div className="max-h-44 overflow-auto flex flex-col gap-0.5">
            {candidates.map((t) => (
              <button key={t.id} onClick={() => attach(t.id)}
                      className="flex items-center gap-2 px-2 py-1 rounded-lg text-left text-xs hover:bg-slate-50">
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: t.color }} />{t.name}
              </button>
            ))}
            {q.trim() && !all.some((t) => t.name.toLowerCase() === q.trim().toLowerCase()) && (
              <button onClick={createAndAttach} className="px-2 py-1 rounded-lg text-left text-xs font-bold text-primary hover:bg-primary/5">
                + Create “{q.trim()}”
              </button>
            )}
            {!candidates.length && !q && <div className="px-2 py-1 text-[11px] text-ink-tertiary">Type to create a tag</div>}
          </div>
        </div>
      )}
    </div>
  );
}
