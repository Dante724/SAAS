// Bench notes: the technician's working notes (team only) and the conclusion the customer sees.
// Edited in a sheet; the phone keyboard's microphone covers dictation. Saving is online only so a
// stale queued copy can't overwrite someone else's later edit.
import { useEffect, useState } from "react";
import { NotebookPen } from "lucide-react";
import { api } from "../../api/client";
import { Card, PrimaryButton, Sheet } from "./ui";
import { haptic } from "../lib/scanner";
import { useToast } from "../lib/useGo";

const area = "w-full min-h-[7rem] rounded-2xl border border-slate-200 bg-white px-4 py-3 text-[1rem] text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15 resize-none";

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

  async function save() {
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Notes need a connection to save.", tone: "warn" }); return; }
    setBusy(true);
    try {
      await api.patch(`/orders/${encodeURIComponent(orderId)}/notes`, { tech_notes: notes.trim(), conclusion: concl.trim() });
      haptic();
      onSaved({ tech_notes: notes.trim(), conclusion: concl.trim() });
      setOpen(false);
    } catch (e: any) {
      toast({ title: "Couldn't save the notes", body: e?.message, tone: "error" });
    } finally { setBusy(false); }
  }

  const empty = !techNotes && !conclusion;
  return (
    <div className="mt-4">
      <h2 className="px-1 mb-2 text-[0.8125rem] font-semibold text-ink-tertiary">Bench notes</h2>
      <Card className="p-4">
        {empty ? <p className="text-[0.9375rem] text-ink-tertiary">No notes yet.</p> : (
          <div className="space-y-3">
            {techNotes && <div><p className="text-[0.8125rem] text-ink-tertiary">Working notes · team only</p><p className="text-[0.9375rem] text-ink-primary whitespace-pre-wrap break-words">{techNotes}</p></div>}
            {conclusion && <div><p className="text-[0.8125rem] text-ink-tertiary">For the customer</p><p className="text-[0.9375rem] text-ink-primary whitespace-pre-wrap break-words">{conclusion}</p></div>}
          </div>
        )}
        {!readOnly && (
          <button onClick={() => setOpen(true)} className="mt-3 min-h-[2.75rem] inline-flex items-center gap-2 font-semibold text-primary">
            <NotebookPen size={18} />{empty ? "Add notes" : "Edit notes"}
          </button>
        )}
      </Card>
      <Sheet open={open} onClose={() => setOpen(false)} title="Bench notes">
        <div className="px-2 space-y-4">
          <label className="block">
            <span className="mb-1.5 block px-1 text-[0.8125rem] font-semibold text-ink-tertiary">Working notes · only your team sees these</span>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} className={area} placeholder="What you found, what you tried, readings…" />
          </label>
          <label className="block">
            <span className="mb-1.5 block px-1 text-[0.8125rem] font-semibold text-ink-tertiary">Conclusion · the customer sees this</span>
            <textarea value={concl} onChange={(e) => setConcl(e.target.value)} className={area} placeholder="e.g. Replaced the screen and tested touch, cameras and Face ID." />
          </label>
          <p className="px-1 text-[0.8125rem] text-ink-tertiary">Tip: tap the microphone on your keyboard to dictate.</p>
          <PrimaryButton onClick={save} disabled={busy}>{busy ? "Saving…" : "Save notes"}</PrimaryButton>
        </div>
      </Sheet>
    </div>
  );
}
