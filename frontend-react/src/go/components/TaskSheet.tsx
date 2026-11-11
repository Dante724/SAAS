// New task: what, when, who. Assigning someone else needs can_assign_staff (the server checks too).
import { useEffect, useMemo, useRef, useState } from "react";
import { Link2 } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { PrimaryButton, Sheet, goField } from "./ui";
import Chip from "./Chip";
import { announceChange, useStaff, useToast } from "../lib/useGo";
import { newKey } from "../lib/photos";
import { fromShopWall, shopToday, useShopTz } from "../../lib/shopTime";
import { addDays } from "../../pages/tasks/taskModel";

type When = "none" | "today" | "tomorrow" | "pick";

/** Due date for a quick choice, on the SHOP's clock. Today / Tomorrow send the shop date alone
 *  (the server makes it the end of that shop day, the same as the web's quick add); a picked
 *  time goes with the shop's UTC offset so the server stores the right instant. */
export function dueFor(when: When, picked: string, now = new Date(), tz = "UTC"): string | null {
  if (when === "none") return null;
  if (when === "pick") return fromShopWall(picked || null, tz);
  const today = shopToday(tz, now);
  return when === "tomorrow" ? addDays(today, 1) : today;
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
  const tz = useShopTz();

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
    if (!title.trim() || busy || !tz) return;   // wait for the shop's clock
    setBusy(true);
    try {
      await api.postOnce("/tasks", {
        title: title.trim(), assigned_to_id: who || me?.id, due_at: dueFor(when, picked, new Date(), tz || "UTC"), order_id: orderId || null,
      }, key.current);
      toast({ title: "Task added" });
      announceChange("tasks");
      onClose();
    } catch (e: any) {
      toast({ title: "Couldn't add the task", body: e?.message, tone: "error" });
    } finally { setBusy(false); }
  }

  return (
    <Sheet open={open} onClose={onClose} title="New task">
      <div className="px-2 space-y-4">
        <input ref={input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What needs doing?" enterKeyHint="done"
          onKeyDown={(e) => { if (e.key === "Enter") save(); }}
          className={goField()} />
        {orderId && <p className="inline-flex items-center gap-1.5 text-go-sub text-ink-secondary"><Link2 size={15} />On job {orderLabel}</p>}
        <div>
          <p className="mb-2 text-go-sub font-semibold text-ink-tertiary">When</p>
          <div className="flex flex-wrap gap-2">
            {(["today", "tomorrow", "none", "pick"] as When[]).map((w) => (
              <Chip key={w} on={when === w} tick onClick={() => setWhen(w)}>
                {{ today: "Today", tomorrow: "Tomorrow", none: "No date", pick: "Other date" }[w]}
              </Chip>
            ))}
          </div>
          {when === "pick" && (
            <input type="datetime-local" value={picked} onChange={(e) => setPicked(e.target.value)}
              aria-label="Due date and time" className={goField("mt-2")} />
          )}
        </div>
        <div>
          <p className="mb-2 text-go-sub font-semibold text-ink-tertiary">Who</p>
          <div className="flex flex-wrap gap-2">
            {people.map((p) => (
              <Chip key={p.id} on={who === p.id} tick onClick={() => setWho(p.id)}>{p.name}</Chip>
            ))}
          </div>
        </div>
        <PrimaryButton onClick={save} disabled={!title.trim() || busy}>{busy ? "Adding…" : "Add task"}</PrimaryButton>
      </div>
    </Sheet>
  );
}
