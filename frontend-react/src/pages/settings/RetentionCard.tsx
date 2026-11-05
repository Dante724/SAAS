// Data retention (Settings → General). How long the shop keeps old notifications, call records,
// messages and the activity log. Blank keeps them for good (the default); a daily job deletes
// anything older than the number of days set here.
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { alertDialog } from "../../lib/dialog";

type Kind = { key: string; label: string; days: number | null };
type Data = { kinds: Kind[]; min_days: number; max_days: number };

const field = "glass-input w-28 px-3 py-2 rounded-lg text-xs tabular-nums";

export default function RetentionCard({ Card }: { Card: (p: { title: string; children: any }) => any }) {
  const [data, setData] = useState<Data | null>(null);
  const [days, setDays] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  function show(d: Data) {
    setData(d);
    setDays(Object.fromEntries(d.kinds.map((k) => [k.key, k.days == null ? "" : String(k.days)])));
  }
  useEffect(() => { api.get<Data>("/compliance/retention").then(show).catch(() => setData(null)); }, []);
  if (!data) return null;
  async function save() {
    setBusy(true); setSaved(false);
    try {
      const body = Object.fromEntries(Object.entries(days).map(([k, v]) => [k, v.trim() ? Number(v) : null]));
      show(await api.put<Data>("/compliance/retention", { days: body }));
      setSaved(true);
    } catch (e: any) { alertDialog(e.message); } finally { setBusy(false); }
  }
  return (
    <Card title="Keep old records">
      <div className="p-4 space-y-3 text-xs">
        <p className="text-ink-tertiary">Leave a box empty to keep those records for good. Anything older than the days you set is deleted once a day. Between {data.min_days} and {data.max_days} days.</p>
        {data.kinds.map((k) => (
          <div key={k.key} className="flex items-center justify-between gap-3">
            <label htmlFor={`keep-${k.key}`} className="font-semibold text-ink-secondary">{k.label}</label>
            <div className="flex items-center gap-2">
              <input id={`keep-${k.key}`} type="number" min={data.min_days} max={data.max_days} value={days[k.key] ?? ""}
                onChange={(e) => { setDays({ ...days, [k.key]: e.target.value }); setSaved(false); }} placeholder="Keep" className={field} />
              <span className="text-ink-tertiary">days</span>
            </div>
          </div>
        ))}
        <div className="flex items-center gap-3 pt-1">
          <button onClick={save} disabled={busy} className="px-3 py-2 rounded-lg bg-primary text-white font-semibold disabled:opacity-60">Save</button>
          {saved && <span role="status" className="text-emerald-700">Saved</span>}
        </div>
      </div>
    </Card>
  );
}
