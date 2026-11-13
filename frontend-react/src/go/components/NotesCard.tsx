// Bench notes: the technician's working notes (team only) and the conclusion the customer sees.
// Edited in a sheet; the phone keyboard's microphone covers dictation. Saving is online only so a
// stale queued copy can't overwrite someone else's later edit.
import { useEffect, useState } from "react";
import { NotebookPen } from "lucide-react";
import { api } from "../../api/client";
import { isStaleOrder, patchOrderHeader, rememberOrderVersion } from "../../pages/orders/orderVersion";
import { Card, GoButton, PrimaryButton, Sheet, goArea } from "./ui";
import { haptic } from "../lib/scanner";
import { useToast } from "../lib/useGo";
import EmptyState from "../../components/EmptyState";

const area = goArea("min-h-[7rem]");

export default function NotesCard({ orderId, techNotes, conclusion, readOnly, onSaved }: {
  orderId: string; techNotes?: string | null; conclusion?: string | null; readOnly?: boolean;
  onSaved: (n: { tech_notes: string; conclusion: string }) => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState(techNotes || "");
  const [concl, setConcl] = useState(conclusion || "");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setNotes(techNotes || ""); setConcl(conclusion || ""); } }, [open, techNotes, conclusion]);
  // WO-03: read the job's current notes and version as the editor opens, so the save can say
  // "this is what I edited" (If-Match) and never overwrite a colleague's later save.
  async function fetchCurrent() {
    const o = await api.get<any>(`/orders/${encodeURIComponent(orderId)}`);
    rememberOrderVersion(orderId, o?.updated_at);
    return { tech_notes: o?.tech_notes || "", conclusion: o?.conclusion || "" };
  }
  useEffect(() => {
    if (!open) return;
    let live = true;
    fetchCurrent().then((cur) => {
      if (!live) return;
      if (cur.tech_notes !== (techNotes || "") || cur.conclusion !== (conclusion || "")) {
        setNotes(cur.tech_notes); setConcl(cur.conclusion); onSaved(cur);
      }
    }).catch(() => { /* offline: the save says so */ });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, orderId]);

  async function save() {
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Notes need a connection to save.", tone: "warn" }); return; }
    setBusy(true);
    try {
      await patchOrderHeader(orderId, `/orders/${encodeURIComponent(orderId)}/notes`, { tech_notes: notes.trim(), conclusion: concl.trim() });
      haptic();
      onSaved({ tech_notes: notes.trim(), conclusion: concl.trim() });
      setOpen(false);
    } catch (e: any) {
      if (isStaleOrder(e)) {
        // Someone else saved the job meanwhile: show their notes (reloaded) and say so.
        try { const cur = await fetchCurrent(); setNotes(cur.tech_notes); setConcl(cur.conclusion); onSaved(cur); } catch { /* keep the text */ }
        toast({ title: "Notes changed on another screen", body: e.message, tone: "warn" });
      } else {
        toast({ title: "Couldn't save the notes", body: e?.message, tone: "error" });
      }
    } finally { setBusy(false); }
  }

  const empty = !techNotes && !conclusion;
  return (
    <div className="mt-4">
      <h2 className="px-4 mb-1.5 text-go-sub font-medium text-ink-tertiary">Bench notes</h2>
      <Card className="p-4">
        {empty ? <EmptyState size="inline" title="No notes yet." /> : (
          <div className="space-y-3">
            {techNotes && <div><p className="text-go-sub text-ink-tertiary">Working notes · team only</p><p className="text-go-body text-ink-primary whitespace-pre-wrap break-words">{techNotes}</p></div>}
            {conclusion && <div><p className="text-go-sub text-ink-tertiary">For the customer</p><p className="text-go-body text-ink-primary whitespace-pre-wrap break-words">{conclusion}</p></div>}
          </div>
        )}
        {!readOnly && (
          <GoButton variant="link" onClick={() => setOpen(true)} className="mt-3">
            <NotebookPen size={18} />{empty ? "Add notes" : "Edit notes"}
          </GoButton>
        )}
      </Card>
      <Sheet open={open} onClose={() => setOpen(false)} title="Bench notes">
        <div className="px-2 space-y-4">
          <label className="block">
            <span className="mb-1.5 block px-1 text-go-sub font-semibold text-ink-tertiary">Working notes · only your team sees these</span>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} className={area} placeholder="What you found, what you tried, readings…" />
          </label>
          <label className="block">
            <span className="mb-1.5 block px-1 text-go-sub font-semibold text-ink-tertiary">Conclusion · the customer sees this</span>
            <textarea value={concl} onChange={(e) => setConcl(e.target.value)} className={area} placeholder="e.g. Replaced the screen and tested touch, cameras and Face ID." />
          </label>
          <p className="px-1 text-go-sub text-ink-tertiary">Tip: tap the microphone on your keyboard to dictate.</p>
          <PrimaryButton onClick={save} disabled={busy}>{busy ? "Saving…" : "Save notes"}</PrimaryButton>
        </div>
      </Sheet>
    </div>
  );
}
