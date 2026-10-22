import { useEffect, useState } from "react";
import { api } from "../api/client";
import CustomFields from "./CustomFields";

// The work order's own form fields (Passcode, Accessories…) on the job card, editable after intake.
// Shows the fields the form puts on the job card (Settings → Forms → Customize → "Shows on"), plus
// any marked "Before closing" — those must be fillable here or the order could never be closed.
type LayoutItem = { ref: string; kind: string; key: string; label: string; visible: boolean; require?: string; field?: any };

export default function JobCardFields({ orderId, typeId, value, onSaved, onFlash }: {
  orderId: string; typeId?: string | null; value?: Record<string, any> | null;
  onSaved: (v: Record<string, any>) => void; onFlash: (m: string) => void;
}) {
  const [layout, setLayout] = useState<LayoutItem[] | null>(null);
  const [draft, setDraft] = useState<Record<string, any>>(value || {});
  const [busy, setBusy] = useState(false);
  useEffect(() => { setDraft(value || {}); }, [value]);
  useEffect(() => {
    if (!typeId) { setLayout(null); return; }
    api.get<{ items: LayoutItem[] }>(`/forms/layout?form=order&type_id=${typeId}`).then((r) => setLayout(r.items || [])).catch(() => setLayout(null));
  }, [typeId]);
  if (!typeId || !layout) return null;

  const onCard = (i: LayoutItem) => i.require === "close" || i.field?.show_on_job_card !== false;
  const ids = layout.filter((i) => i.visible && (i.kind === "custom" || i.kind === "header") && onCard(i)).map((i) => i.ref.slice(3));
  // A section heading with nothing under it on the card would just be noise.
  const shown = ids.filter((id, n) => {
    const it = layout.find((i) => i.ref === `cf:${id}`);
    return it?.kind !== "header" || (n + 1 < ids.length && layout.find((i) => i.ref === `cf:${ids[n + 1]}`)?.kind !== "header");
  });
  if (!shown.some((id) => layout.find((i) => i.ref === `cf:${id}`)?.kind === "custom")) return null;
  const closeIds = layout.filter((i) => i.require === "close" && i.kind === "custom").map((i) => i.ref.slice(3));
  const dirty = JSON.stringify(draft) !== JSON.stringify(value || {});

  async function save() {
    setBusy(true);
    try {
      const r = await api.patch<{ custom_data: Record<string, any> }>(`/orders/${orderId}/custom-data`, { custom_data: draft });
      onSaved(r.custom_data); onFlash("Details saved");
    } catch (e: any) { onFlash(`Failed: ${e.message}`); } finally { setBusy(false); }
  }

  return (
    <div className="p-4 rounded-xl bg-slate-50/80 border border-slate-200 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold text-ink-tertiary ">Details</span>
        {dirty && <button type="button" disabled={busy} onClick={save} className="px-3 py-1 rounded-lg bg-primary text-white text-[11px] font-bold disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>}
      </div>
      <CustomFields typeId={typeId} only={shown} bare closeIds={closeIds} value={draft} onChange={setDraft} creating={false} />
    </div>
  );
}
