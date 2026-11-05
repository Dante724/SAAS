// Data retention (Settings → General). How long the shop keeps old notifications, call records,
// messages and the activity log. Blank keeps them for good (the default); a daily job deletes
// anything older than the number of days set here.
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { alertDialog } from "../../lib/dialog";
import Section from "../../components/Section";
import SaveFooter from "../../components/SaveFooter";
import { SettingRow } from "../../components/SettingRow";
import { sectionFooter } from "./kit";

type Kind = { key: string; label: string; days: number | null };
type Data = { kinds: Kind[]; min_days: number; max_days: number };

type CardT = (p: { title: string; children: any }) => any;
// The shared settings card, unless the caller passes its own.
const DefaultCard: CardT = ({ title, children }) => <Section title={title}>{children}</Section>;

export default function RetentionCard({ Card = DefaultCard }: { Card?: CardT }) {
  const [data, setData] = useState<Data | null>(null);
  const [days, setDays] = useState<Record<string, string>>({});
  const [savedDays, setSavedDays] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  function show(d: Data) {
    setData(d);
    const v = Object.fromEntries(d.kinds.map((k) => [k.key, k.days == null ? "" : String(k.days)]));
    setDays(v); setSavedDays(v);
  }
  useEffect(() => { api.get<Data>("/compliance/retention").then(show).catch(() => setData(null)); }, []);
  if (!data) return null;
  const dirty = JSON.stringify(days) !== JSON.stringify(savedDays);
  async function save() {
    setBusy(true); setMsg(null);
    try {
      const body = Object.fromEntries(Object.entries(days).map(([k, v]) => [k, v.trim() ? Number(v) : null]));
      show(await api.put<Data>("/compliance/retention", { days: body }));
      setMsg({ ok: true, text: "Saved" });
    } catch (e: any) { alertDialog(e.message); } finally { setBusy(false); }
  }
  return (
    <Card title="Keep old records">
      <p className="text-xs text-ink-tertiary mb-3">Leave a box empty to keep those records for good. Anything older than the days you set is deleted once a day. Between {data.min_days} and {data.max_days} days.</p>
      <div className="divide-y divide-slate-100">
        {data.kinds.map((k) => (
          <SettingRow key={k.key} label={k.label} htmlFor={`keep-${k.key}`}>
            <div className="glass-input h-9 w-32 rounded-xl flex items-center pr-3 focus-within:ring-2 focus-within:ring-primary/30">
              <input id={`keep-${k.key}`} type="number" min={data.min_days} max={data.max_days} value={days[k.key] ?? ""} placeholder="Keep"
                onChange={(e) => { setDays({ ...days, [k.key]: e.target.value }); setMsg(null); }}
                className="w-full min-w-0 bg-transparent pl-3 text-sm tabular-nums text-ink-primary outline-none" />
              <span className="text-xs text-ink-tertiary shrink-0">days</span>
            </div>
          </SettingRow>
        ))}
      </div>
      <SaveFooter className={sectionFooter} dirty={dirty} busy={busy} msg={msg} onSave={save} onDiscard={() => { setDays(savedDays); setMsg(null); }} />
    </Card>
  );
}
