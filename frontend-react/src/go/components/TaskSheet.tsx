// New task: what, when, who. Assigning someone else needs can_assign_staff (the server checks too).
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Link2 } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { PrimaryButton, Sheet } from "./ui";
import { announceChange, useStaff, useToast } from "../lib/useGo";
import { newKey } from "../lib/photos";
import { isoDateTimeLocal } from "../../lib/localDate";

type When = "none" | "today" | "tomorrow" | "pick";

/** Due date for a quick choice: end of the working day (18:00 local). */
export function dueFor(when: When, picked: string, now = new Date()): string | null {
  if (when === "none") return null;
  if (when === "pick") return picked || null;
  const d = new Date(now);
  if (when === "tomorrow") d.setDate(d.getDate() + 1);
  d.setHours(18, 0, 0, 0);
  return isoDateTimeLocal(d);
}

export default function TaskSheet({ open, onClose, orderId, orderLabel }: { open: boolean; onClose: () => void; orderId?: string; orderLabel?: string }) {
  const { me, can } = useMe();
  const toast = useToast();
  const staff = useStaff();
  const [title, setTitle] = useState("");
  const [when, setWhen] = useState<When>("today");
  const [picked, setPicked] = useState("");
  const [who, setWho] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const key = useRef(newKey());
  const input = useRef<HTMLInputElement>(null);
  const mayAssign = can("can_assign_staff");

  useEffect(() => {
    if (!open) return;
    setTitle(""); setWhen("today"); setPicked(""); setWho(me?.id || ""); key.current = newKey();
    setTimeout(() => input.current?.focus(), 250);
  }, [open, me?.id]);

  const people = useMemo(() => {
    const others = staff.filter((s) => s.id !== me?.id);
    return [{ id: me?.id || "", name: "Me" }, ...(mayAssign ? others : [])];
  }, [staff, me?.id, mayAssign]);

  async function save() {
    if (!title.trim() || busy) return;
    setBusy(true);
    try {
      await api.postOnce("/tasks", {
        title: title.trim(), assigned_to_id: who || me?.id, due_at: dueFor(when, picked), order_id: orderId || null,
      }, key.current);
      toast({ title: "Task added" });
      announceChange("tasks");
      onClose();
    } catch (e: any) {
      toast({ title: "Couldn't add the task", body: e?.message, tone: "error" });
    } finally { setBusy(false); }
  }

  const chip = (on: boolean) => `min-h-[2.75rem] px-4 rounded-full border text-go-body font-medium inline-flex items-center gap-1.5 ${on ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`;

  return (
    <Sheet open={open} onClose={onClose} title="New task">
      <div className="px-2 space-y-4">
        <input ref={input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What needs doing?" enterKeyHint="done"
          onKeyDown={(e) => { if (e.key === "Enter") save(); }}
          className="w-full min-h-[3.25rem] rounded-xl border border-slate-200 bg-white px-4 text-go-body text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15" />
        {orderId && <p className="inline-flex items-center gap-1.5 text-go-sub text-ink-secondary"><Link2 size={15} />On job {orderLabel}</p>}
        <div>
          <p className="mb-2 text-go-sub font-semibold text-ink-tertiary">When</p>
          <div className="flex flex-wrap gap-2">
            {(["today", "tomorrow", "none", "pick"] as When[]).map((w) => (
              <button key={w} onClick={() => setWhen(w)} className={chip(when === w)}>
                {when === w && <Check size={16} />}{{ today: "Today", tomorrow: "Tomorrow", none: "No date", pick: "Pick…" }[w]}
              </button>
            ))}
          </div>
          {when === "pick" && (
            <input type="datetime-local" value={picked} onChange={(e) => setPicked(e.target.value)}
              className="mt-2 w-full min-h-[3rem] rounded-xl border border-slate-200 bg-white px-4 text-ink-primary" />
          )}
        </div>
        <div>
          <p className="mb-2 text-go-sub font-semibold text-ink-tertiary">Who</p>
          <div className="flex flex-wrap gap-2">
            {people.map((p) => (
              <button key={p.id} onClick={() => setWho(p.id)} className={chip(who === p.id)}>{who === p.id && <Check size={16} />}{p.name}</button>
            ))}
          </div>
        </div>
        <PrimaryButton onClick={save} disabled={!title.trim() || busy}>{busy ? "Adding…" : "Add task"}</PrimaryButton>
      </div>
    </Sheet>
  );
}
