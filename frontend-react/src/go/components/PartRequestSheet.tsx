// "This job needs a part": becomes an urgent task on the job for whoever orders stock (the job's
// manager, or the whole shop). POST /api/tasks/part-request with an Idempotency-Key, so a retried
// tap asks once. Optionally moves the job to a "waiting for parts" status at the same time.
import { useEffect, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { api } from "../../api/client";
import { PrimaryButton, Sheet } from "./ui";
import { newKey } from "../lib/photos";
import { haptic } from "../lib/scanner";
import { announceChange, useToast } from "../lib/useGo";

const field = "w-full min-h-[3rem] rounded-2xl border border-slate-200 bg-white px-4 text-[1rem] text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15";

export default function PartRequestSheet({ open, onClose, orderId, initialName, waitingStatus, onWaiting }: {
  open: boolean; onClose: () => void; orderId: string; initialName?: string;
  /** A "waiting for parts" status this job may move to, if the shop has one. */
  waitingStatus?: { key: string; name: string } | null;
  onWaiting?: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [qty, setQty] = useState(1);
  const [note, setNote] = useState("");
  const [wait, setWait] = useState(true);
  const [busy, setBusy] = useState(false);
  const key = useRef(newKey());
  useEffect(() => {
    if (!open) return;
    setName(initialName || ""); setQty(1); setNote(""); setWait(true); key.current = newKey();
  }, [open, initialName]);

  async function send() {
    if (!name.trim() || busy) return;
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Asking for a part needs a connection.", tone: "warn" }); return; }
    setBusy(true);
    try {
      await api.postOnce("/tasks/part-request", { order_id: orderId, description: name.trim(), qty, note: note.trim() || null }, key.current);
      haptic();
      announceChange("tasks");
      toast({ title: "Part requested", body: "It's on the job as an urgent task for whoever orders stock." });
      if (wait && waitingStatus) onWaiting?.();
      onClose();
    } catch (e: any) {
      toast({ title: "Couldn't ask for the part", body: e?.message, tone: "error" });
    } finally { setBusy(false); }
  }

  return (
    <Sheet open={open} onClose={onClose} title="Ask for a part">
      <div className="px-2 space-y-4">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Which part? e.g. iPhone 13 screen" aria-label="Part" className={field} autoFocus />
        <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-2">
          <span className="text-[0.9375rem] text-ink-secondary">How many</span>
          <div className="flex items-center gap-4">
            <button onClick={() => setQty((n) => Math.max(1, n - 1))} aria-label="One less" className="h-11 w-11 rounded-full border border-slate-200 inline-flex items-center justify-center"><Minus size={18} /></button>
            <span className="w-6 text-center text-[1.25rem] font-bold tabular-nums">{qty}</span>
            <button onClick={() => setQty((n) => Math.min(99, n + 1))} aria-label="One more" className="h-11 w-11 rounded-full border border-slate-200 inline-flex items-center justify-center"><Plus size={18} /></button>
          </div>
        </div>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Note for whoever orders it (colour, supplier, how urgent…)" aria-label="Note"
          className={`${field} py-3 min-h-[5rem] resize-none`} />
        {waitingStatus && (
          <label className="flex min-h-[3rem] items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4">
            <input type="checkbox" checked={wait} onChange={(e) => setWait(e.target.checked)} className="h-6 w-6 accent-primary" />
            <span className="text-[0.9375rem] text-ink-primary">Move the job to “{waitingStatus.name}”</span>
          </label>
        )}
        <PrimaryButton onClick={send} disabled={!name.trim() || busy}>{busy ? "Sending…" : "Ask for it"}</PrimaryButton>
      </div>
    </Sheet>
  );
}
