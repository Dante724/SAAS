// New inquiry: someone asked about a repair but there's no job yet. Who, what kind, which device,
// what they asked for. Saved through the offline queue with one idempotency key per form, so a
// retry (or a replay after being offline) never makes a second inquiry.
import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { api } from "../../api/client";
import { PrimaryButton, Sheet } from "./ui";
import { goQueue } from "../lib/offlineQueue";
import { newKey } from "../lib/photos";
import { useToast } from "../lib/useGo";
import { createInquiry, emptyDraft, type InquiryDraft, type InquiryType } from "../lib/inquiries";

export const INQUIRIES_CHANGED = "go:inquiries";

let typeCache: Promise<InquiryType[]> | null = null;
function loadTypes() {
  typeCache ??= api.get<{ items: InquiryType[] }>("/leads/types")
    .then((r) => (r.items || []).filter((t) => t.active))
    .catch((e) => { typeCache = null; throw e; });
  return typeCache;
}

const field = "w-full min-h-[3rem] rounded-xl border border-slate-200 bg-white px-4 text-go-body text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15";
const chip = (on: boolean) => `min-h-[2.75rem] px-4 rounded-full border text-go-body font-medium inline-flex items-center gap-1.5 ${on ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`;
const Label = ({ children }: { children: string }) => <span className="mb-1.5 block px-1 text-go-sub font-semibold text-ink-tertiary">{children}</span>;

export default function InquirySheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const [d, setD] = useState<InquiryDraft>(emptyDraft());
  const [types, setTypes] = useState<InquiryType[]>([]);
  const [busy, setBusy] = useState(false);
  const key = useRef(newKey());
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setD(emptyDraft()); key.current = newKey();
    loadTypes().then(setTypes).catch(() => setTypes([]));
    setTimeout(() => first.current?.focus(), 250);
  }, [open]);

  const set = (k: keyof InquiryDraft) => (e: { target: { value: string } }) => setD((x) => ({ ...x, [k]: e.target.value }));

  async function save() {
    if (!d.name.trim() || busy) return;
    setBusy(true);
    try {
      const r = await createInquiry((m, u, b) => goQueue().mutate(m, u, b), d, key.current);
      if (!r.ok) { toast({ title: "Couldn't save the inquiry", body: r.data?.detail && String(r.data.detail), tone: "error" }); return; }
      toast(r.queued ? { title: "Inquiry saved on this phone", body: "It will send when you're back online.", tone: "warn" } : { title: "Inquiry added" });
      window.dispatchEvent(new CustomEvent(INQUIRIES_CHANGED));
      onClose();
    } finally { setBusy(false); }
  }

  return (
    <Sheet open={open} onClose={onClose} title="New inquiry">
      <div className="px-2 space-y-4">
        <div className="grid grid-cols-1 gap-3">
          <label><Label>Name</Label>
            <input ref={first} value={d.name} onChange={set("name")} placeholder="Who's asking?" autoComplete="off" className={field} /></label>
          <label><Label>Phone</Label>
            <input value={d.phone} onChange={set("phone")} type="tel" inputMode="tel" placeholder="So you can call back" autoComplete="off" className={field} /></label>
        </div>
        {types.length > 0 && (
          <div role="group" aria-label="Type">
            <Label>Type</Label>
            <div className="flex flex-wrap gap-2">
              {types.map((t) => {
                const on = d.typeId === t.id;
                return <button key={t.id} type="button" aria-pressed={on} onClick={() => setD((x) => ({ ...x, typeId: on ? "" : t.id }))} className={chip(on)}>{on && <Check size={16} />}{t.name}</button>;
              })}
            </div>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <label><Label>Brand</Label><input value={d.brand} onChange={set("brand")} placeholder="Apple" autoComplete="off" className={field} /></label>
          <label><Label>Model</Label><input value={d.model} onChange={set("model")} placeholder="iPhone 14" autoComplete="off" className={field} /></label>
        </div>
        <label className="block"><Label>Serial or IMEI</Label>
          <input value={d.serial} onChange={set("serial")} placeholder="Optional" autoComplete="off" autoCapitalize="characters" className={`${field} font-mono`} /></label>
        <label className="block"><Label>What they asked for</Label>
          <textarea value={d.inquiry} onChange={set("inquiry")} rows={3} placeholder="Screen price, how long it takes…" className={`${field} py-3 min-h-[5.5rem]`} /></label>
        <PrimaryButton onClick={save} disabled={!d.name.trim() || busy}>{busy ? "Saving…" : "Add inquiry"}</PrimaryButton>
      </div>
    </Sheet>
  );
}
