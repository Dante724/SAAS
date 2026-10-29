// Comments and files on a work order (Activity tab). New ones start private or shown to the
// customer per the shop default (Settings › General › Comments and files); each one can be
// switched on its own. Shown-to-customer items appear on the customer's tracking page.
// Backend: GET/POST /feed/order/{id}, PATCH /feed/item/{id}.
import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { readAsDataUrl } from "../../lib/files";
import AuthedImage from "../../components/AuthedImage";
import { fmtServerTime } from "../../lib/serverTime";

type Vis = "private" | "client";
export type FeedItem = {
  id: string; item_type: string; body?: string | null; media_data?: string | null; media_url?: string | null;
  media_name?: string | null; media_mime?: string | null; visibility: Vis; author_name?: string | null;
  author_id?: string | null; created_at: string;
};

const SHOWN = new Set(["comment", "photo", "file"]);
const when = (iso: string) => fmtServerTime(iso);   // UTC instant, shown on the viewer's clock
const isImage = (it: FeedItem) => it.item_type === "photo" || (it.media_mime || "").startsWith("image/");

export function VisibilityToggle({ value, onChange, disabled, compact }: { value: Vis; onChange: (v: Vis) => void; disabled?: boolean; compact?: boolean }) {
  const pub = value === "client";
  return (
    <button type="button" disabled={disabled} onClick={() => onChange(pub ? "private" : "client")}
      aria-label={pub ? "Shown to customer. Make private" : "Private. Show to customer"}
      title={pub ? "The customer sees this on their tracking page" : "Only your team sees this"}
      className={`inline-flex items-center gap-1 rounded-lg border font-semibold transition-colors disabled:opacity-50 ${compact ? "px-1.5 py-0.5 text-[11px]" : "px-2.5 py-1.5 text-xs"} ${pub ? "bg-emerald-50 border-emerald-200 text-emerald-700 hover:bg-emerald-100" : "bg-white border-slate-200 text-ink-secondary hover:bg-slate-50"}`}>
      <M n={pub ? "visibility" : "lock"} c={compact ? "text-[12px]" : "text-[14px]"} />{pub ? "Customer sees" : "Private"}
    </button>
  );
}

export default function NotesPanel({ orderId, onFlash }: { orderId: string; onFlash: (m: string) => void }) {
  const { me, can } = useMe();
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [defaults, setDefaults] = useState<{ comment: Vis; file: Vis }>({ comment: "private", file: "private" });
  const [text, setText] = useState("");
  const [vis, setVis] = useState<Vis>("private");
  const touched = useRef(false);          // the poster picked a visibility: don't reset it to the default
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api.get<{ items: FeedItem[] }>(`/feed/order/${orderId}`)
    .then((r) => setItems((r.items || []).filter((i) => SHOWN.has(i.item_type)))).catch(() => setItems([]));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId]);
  useEffect(() => {
    api.get<any>("/staff/company").then((c) => {
      const d = { comment: (c?.default_comment_visibility === "public" ? "client" : "private") as Vis,
                  file: (c?.default_file_visibility === "public" ? "client" : "private") as Vis };
      setDefaults(d);
      if (!touched.current) setVis(d.comment);
    }).catch(() => {});
  }, []);

  async function post() {
    const body = text.trim(); if (!body) return;
    setBusy(true);
    try {
      await api.post(`/feed/order/${orderId}`, { item_type: "comment", body, visibility: vis });
      setText(""); touched.current = false; setVis(defaults.comment); await load();
    } catch (e: any) { onFlash(e.message || "Could not post"); }
    finally { setBusy(false); }
  }
  async function attach(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const data = await readAsDataUrl(f);
        await api.post(`/feed/order/${orderId}`, {
          item_type: f.type.startsWith("image/") ? "photo" : "file", media_data: data, media_name: f.name, media_mime: f.type,
          visibility: defaults.file,
        });
      }
      await load();
      onFlash(defaults.file === "client" ? "Added. The customer can see it" : "Added as private");
    } catch (e: any) { onFlash(e.message || "Upload failed (file may be too large)"); }
    finally { setBusy(false); }
  }
  async function flip(it: FeedItem, v: Vis) {
    try {
      const r = await api.patch<FeedItem>(`/feed/item/${it.id}`, { visibility: v });
      setItems((xs) => (xs || []).map((x) => (x.id === it.id ? { ...x, visibility: r.visibility } : x)));
    } catch (e: any) { onFlash(e.message || "Could not change it"); }
  }
  const mayEdit = (it: FeedItem) => it.author_id === me?.id || can("can_delete_orders");

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-100 flex items-center gap-2">
        <M n="sticky_note_2" c="text-base text-primary" />
        <h5 className="text-xs font-bold text-ink-primary">Comments and files</h5>
        {items && items.length > 0 && <span className="text-[11px] text-ink-tertiary tabular-nums">{items.length}</span>}
      </div>
      <div className="max-h-72 overflow-y-auto divide-y divide-slate-100">
        {!items && <div className="text-xs text-ink-tertiary py-5 text-center">Loading…</div>}
        {items?.length === 0 && <div className="text-xs text-ink-tertiary py-5 px-4 text-center">No comments or files yet.</div>}
        {(items || []).map((it) => (
          <div key={it.id} className="px-4 py-2.5 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-[11px] text-ink-tertiary">{it.author_name || "Staff"} · <span className="tabular-nums">{when(it.created_at)}</span></div>
              {it.body && <div className="text-xs text-ink-primary whitespace-pre-wrap break-words mt-0.5">{it.body}</div>}
              {(it.media_url || it.media_data) && (isImage(it)
                ? (it.media_url
                  ? <AuthedImage src={it.media_url} alt={it.media_name || "photo"} className="mt-1.5 w-20 h-20 rounded-lg object-cover border border-slate-200" />
                  : <img src={it.media_data!} alt={it.media_name || "photo"} className="mt-1.5 w-20 h-20 rounded-lg object-cover border border-slate-200" />)
                : <a href={it.media_data || undefined} download={it.media_name || "file"} className="mt-1 inline-flex items-center gap-1 text-xs text-primary font-semibold hover:underline break-all"><M n="attach_file" c="text-[14px]" />{it.media_name || "File"}</a>)}
            </div>
            <VisibilityToggle compact value={it.visibility} disabled={!mayEdit(it)} onChange={(v) => flip(it, v)} />
          </div>
        ))}
      </div>
      <div className="p-2.5 border-t border-slate-100 space-y-2">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="Add a comment…"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); post(); } }}
          className="glass-input w-full py-2 px-3 text-xs rounded-xl resize-none" />
        <div className="flex flex-wrap items-center gap-2">
          <VisibilityToggle value={vis} onChange={(v) => { touched.current = true; setVis(v); }} />
          <input ref={fileRef} type="file" multiple className="hidden" onChange={(e) => { attach(e.target.files); e.currentTarget.value = ""; }} />
          <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}
            title={defaults.file === "client" ? "Files start shown to the customer" : "Files start private"}
            className="px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1 disabled:opacity-50"><M n="attach_file" c="text-[14px]" />Attach</button>
          <button type="button" onClick={post} disabled={busy || !text.trim()}
            className="ml-auto px-3.5 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-40">Post</button>
        </div>
      </div>
    </div>
  );
}
