import { useState } from "react";
import Card, { Badge } from "../../components/Card";
import Button from "../../components/Button";
import Drawer from "../../components/Drawer";
import DateField from "../../components/DateField";
import { Field, Input } from "../../components/Field";
import { api, ApiError } from "../../api/client";
import { isoDateTimeLocal } from "../../lib/localDate";

// Decision 6: an approval is refused (409) while time in its period is flagged for review. The
// refusal lists each item (detail.flagged); this shows them with Confirm (accept as it is) and
// Edit (fix the times). Both go through the edit endpoints, which clear the flag and write the
// audit row. A row drops off when it's done; when none are left the manager approves again.
export type Flagged = {
  kind: "time_entry" | "shift"; id: string; employee_id: string; employee_name: string;
  date: string; reason: string; started_at?: string | null; ended_at?: string | null;
};

const CONFIRM_REASON = "Confirmed as recorded";

/** The flagged items of a refused approval, or null when the error is something else. */
export function flaggedFrom(err: unknown): Flagged[] | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  try {
    const f = JSON.parse(err.body)?.detail?.flagged;
    return Array.isArray(f) && f.length ? f : null;
  } catch { return null; }
}

const dayText = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const path = (f: Flagged) => f.kind === "shift" ? `/timeclock/shifts/${f.id}` : `/timeclock/entries/${f.id}`;
const local = (iso?: string | null) => (iso ? isoDateTimeLocal(new Date(iso)) : "");

export default function NeedsReviewList({ items, onDone }: { items: Flagged[]; onDone: () => void }) {
  const [rows, setRows] = useState(items);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<Flagged | null>(null);

  const drop = (id: string) => setRows((r) => {
    const left = r.filter((x) => x.id !== id);
    if (!left.length) onDone();
    return left;
  });

  async function confirm(f: Flagged) {
    setBusy(f.id); setErr(null);
    try { await api.patch(path(f), f.kind === "shift" ? { reason: CONFIRM_REASON } : {}); drop(f.id); }
    catch (e: any) { setErr(e.message || "Could not confirm"); }
    finally { setBusy(null); }
  }

  if (!rows.length) return null;
  return (
    <section aria-label="Needs review">
      <Card padded={false}>
        <div className="p-3.5 border-b border-slate-100 flex items-center gap-2">
          <span className="text-xs font-bold text-ink-primary">Needs review</span>
          <Badge tone="warning" size="sm">{rows.length}</Badge>
          <span className="text-xs text-ink-tertiary">Confirm or edit each one, then approve again.</span>
        </div>
        {err && <div className="mx-4 mt-3 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        <ul className="divide-y divide-slate-100">
          {rows.map((f) => (
            <li key={`${f.kind}:${f.id}`} className="px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
              <div className="flex items-center gap-2">
                <Button variant="primary" size="sm" busy={busy === f.id} onClick={() => confirm(f)}>Confirm</Button>
                <Button size="sm" disabled={busy === f.id} onClick={() => setEditing(f)}>Edit</Button>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-ink-primary">{f.employee_name}</span>
                  <span className="text-ink-secondary tabular-nums">{dayText(f.date)}</span>
                  {f.kind === "shift" && <Badge size="sm">Shift</Badge>}
                </div>
                <div className="text-ink-tertiary mt-0.5">{f.reason}</div>
              </div>
            </li>
          ))}
        </ul>
      </Card>
      {editing && <EditFlagged item={editing} onClose={() => setEditing(null)} onSaved={() => { const id = editing.id; setEditing(null); drop(id); }} />}
    </section>
  );
}

function EditFlagged({ item, onClose, onSaved }: { item: Flagged; onClose: () => void; onSaved: () => void }) {
  const shift = item.kind === "shift";
  const [start, setStart] = useState(local(item.started_at));
  const [end, setEnd] = useState(local(item.ended_at));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    if (shift && !reason.trim()) { setErr("Say why the shift is being corrected"); return; }
    setBusy(true); setErr(null);
    const body: Record<string, string> = {};
    if (start) body.started_at = new Date(start).toISOString();
    if (end) body.ended_at = new Date(end).toISOString();
    if (shift) body.reason = reason.trim();
    try { await api.patch(path(item), body); onSaved(); }
    catch (e: any) { setErr(e.message || "Could not save"); setBusy(false); }
  }

  const title = shift ? "Edit shift" : "Edit time entry";
  return (
    <Drawer title={title} label={title} size="sm" sub={`${item.employee_name}, ${dayText(item.date)}`} onClose={onClose}
      footer={<><Button variant="primary" busy={busy} onClick={save}>Save</Button><Button onClick={onClose}>Cancel</Button></>}>
      {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      <div className="rounded-lg bg-amber-50 border border-amber-200/60 text-amber-700 text-xs px-3 py-2">{item.reason}</div>
      <Field label="Started at"><DateField mode="datetime" value={start} onChange={setStart} label="Started at" /></Field>
      <Field label="Ended at"><DateField mode="datetime" value={end} onChange={setEnd} label="Ended at" /></Field>
      {shift && <Field label="Reason" htmlFor="nr-reason"><Input id="nr-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. forgot to clock out at 5 pm" /></Field>}
    </Drawer>
  );
}
