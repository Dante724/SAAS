import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { M } from "../../components/Shell";
import Button from "../../components/Button";
import Switch from "../../components/Switch";
import TeklySelect from "../../components/TeklySelect";
import SettingsPage from "../../components/SettingsPage";
import EmptyState, { ErrorState } from "../../components/EmptyState";
import { sectionCard } from "../../components/Section";
import { Badge } from "../../components/Card";
import { useMe, hasFeature } from "../../context/Me";
import {
  followupsApi, isMissing, isRefused, isQuotePreset, moneyIn, whenText,
  type Preset, type PresetStep, type Preview,
} from "../followups/followupsApi";

// Follow-ups Phase 1 — Settings › Follow-ups. Two ready-made follow-ups (devices waiting to be
// collected, quotes not answered). Staff switch each on or off and change its steps. Switching
// one on always shows first which jobs it would touch today; nothing is sent before Turn on.

type Draft = { value: string; unit: "hours" | "days"; enabled: boolean };
const toDraft = (s: PresetStep): Draft =>
  s.after_hours >= 72 && s.after_hours % 24 === 0
    ? { value: String(s.after_hours / 24), unit: "days", enabled: s.enabled }
    : { value: String(s.after_hours), unit: "hours", enabled: s.enabled };
const draftHours = (d: Draft) => Math.max(1, Math.round((Number(d.value) || 0) * (d.unit === "days" ? 24 : 1)));

const quoted = (names: string[]) => names.map((n) => `“${n}”`).join(" or ");

export default function FollowupsSettings({ onOpenBilling }: { onOpenBilling?: () => void }) {
  const { me, can } = useMe();
  const [items, setItems] = useState<Preset[] | null>(null);
  const [failed, setFailed] = useState<"" | "missing" | "refused" | "error">("");
  const [statusNames, setStatusNames] = useState<Record<string, string>>({});

  const planOk = hasFeature(me, "notification_rules");
  const permOk = can("can_manage_settings");
  const locked = !planOk || !permOk;

  const load = () => {
    setFailed("");
    return followupsApi.presets().then((r) => setItems(r.items || []))
      .catch((e) => { setItems(null); setFailed(isMissing(e) ? "missing" : isRefused(e) ? "refused" : "error"); });
  };
  useEffect(() => {
    load();
    api.get<any>("/statuses?record_type=work_order")
      .then((r) => setStatusNames(Object.fromEntries((r.items || r || []).map((s: any) => [String(s.id), s.name]))))
      // silent: without names the cards just leave out the "Starts when…" line.
      .catch(() => setStatusNames({}));
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const replace = (p: Preset) => setItems((xs) => (xs || []).map((x) => (x.key === p.key ? p : x)));

  return (
    <SettingsPage title="Follow-ups"
      sub="Tekly texts customers about jobs that are waiting and puts calls on your team’s list. Each follow-up stops by itself when the job moves on.">
      {!planOk && (
        <div role="note" className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50/80 px-3.5 py-2.5 text-xs text-ink-secondary">
          <M n="lock" c="text-sm text-ink-tertiary" />
          <span className="flex-1 min-w-[200px]">Follow-ups come with the Shop and Pro plans, like your other automations. You can look at them here, but not switch them on.</span>
          {onOpenBilling && <Button size="sm" variant="secondary" onClick={onOpenBilling}>See plans</Button>}
        </div>
      )}
      {planOk && !permOk && (
        <div role="note" className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50/80 px-3.5 py-2.5 text-xs text-ink-secondary">
          <M n="lock" c="text-sm text-ink-tertiary" />
          <span>Only people who can change settings can switch follow-ups on or change their steps. Ask the shop owner.</span>
        </div>
      )}

      {items === null && !failed && <p className="text-xs text-ink-tertiary py-4">Loading follow-ups…</p>}
      {failed === "missing" && (
        <div className={sectionCard}><EmptyState icon={<M n="autorenew" c="text-base" />} title="Follow-ups aren’t available yet"
          body="This server doesn’t have follow-ups yet. They appear here after the next Tekly update." /></div>
      )}
      {failed === "refused" && (
        <div className={sectionCard}><EmptyState icon={<M n="lock" c="text-base" />} title="You can’t change follow-ups"
          body="Your role doesn’t include changing settings, or your plan doesn’t include automations. Ask the shop owner." /></div>
      )}
      {failed === "error" && <div className={sectionCard}><ErrorState title="Follow-ups didn’t load" onRetry={load} /></div>}

      {items?.map((p) => (
        <PresetCard key={p.key} preset={p} locked={locked} statusNames={statusNames} onSaved={replace} />
      ))}
    </SettingsPage>
  );
}

function PresetCard({ preset: p, locked, statusNames, onSaved }: {
  preset: Preset; locked: boolean; statusNames: Record<string, string>; onSaved: (p: Preset) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [preview, setPreview] = useState<Preview | "loading" | null>(null);
  const [showJobs, setShowJobs] = useState(false);
  const [busy, setBusy] = useState<"" | "toggle" | "steps">("");
  const [err, setErr] = useState("");

  const quote = isQuotePreset(p.key);
  const starts = (p.start_status_ids || []).map((id) => statusNames[String(id)]).filter(Boolean);
  const desc = [
    starts.length ? `Starts when a ${quote ? "quote" : "job"} is ${quoted(starts)}.` : "",
    quote ? "Stops when the customer approves or declines." : "Stops when it’s paid and collected.",
  ].filter(Boolean).join(" ");

  async function save(body: Parameters<typeof followupsApi.savePreset>[1], kind: "toggle" | "steps") {
    setBusy(kind); setErr("");
    try { onSaved(await followupsApi.savePreset(p.key, body)); return true; }
    catch (e: any) { setErr(e?.message || "Couldn’t save the follow-up"); return false; }
    finally { setBusy(""); }
  }
  async function openPreview() {
    setPreview("loading"); setShowJobs(false); setErr("");
    try { setPreview(await followupsApi.preview(p.key)); }
    catch (e: any) { setPreview(null); setErr(e?.message || "Couldn’t work out the preview"); }
  }
  function onToggle(on: boolean) {
    if (on) openPreview();          // switching on always goes through the preview first
    else { setPreview(null); save({ enabled: false }, "toggle"); }
  }
  async function turnOn() {
    if (await save({ enabled: true }, "toggle")) setPreview(null);
  }
  function startEdit() {
    setDrafts(Object.fromEntries(p.steps.map((s) => [s.index, toDraft(s)])));
    setEditing(true);
  }
  async function saveSteps() {
    const steps = p.steps.map((s) => ({ index: s.index, after_hours: draftHours(drafts[s.index]), enabled: drafts[s.index].enabled }));
    if (await save({ steps }, "steps")) setEditing(false);
  }
  const setDraft = (i: number, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [i]: { ...d[i], ...patch } }));
  const dirty = editing && p.steps.some((s) => draftHours(drafts[s.index]) !== s.after_hours || drafts[s.index].enabled !== s.enabled);

  const pv = preview && preview !== "loading" ? preview : null;
  const noun = (n: number) => (quote ? (n === 1 ? "quote" : "quotes") : (n === 1 ? "job" : "jobs"));

  return (
    <section aria-label={p.name} className={`${sectionCard} p-4 space-y-3 ${locked ? "opacity-70" : ""}`}>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-ink-primary">{p.name}</h3>
            <Badge tone={p.enabled ? "success" : "neutral"} size="sm" dot>{p.enabled ? "On" : "Off"}</Badge>
          </div>
          <p className="mt-0.5 text-xs text-ink-tertiary">{desc}</p>
        </div>
        <Switch on={p.enabled} label={p.name} disabled={locked || busy === "toggle" || preview === "loading"} onChange={onToggle} />
      </div>

      {!editing && (
        <ul className="flex flex-wrap gap-1.5" aria-label={`${p.name} steps`}>
          {p.steps.map((s) => (
            <li key={s.index} className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-2xs font-semibold ${s.enabled ? "bg-slate-100 text-ink-secondary" : "bg-white border border-dashed border-slate-200 text-ink-tertiary"}`}
              title={s.enabled ? undefined : "This step is off"}>
              <M n={s.action === "task" ? "call" : s.action === "email" ? "mail" : "sms"} c={`text-xs ${s.enabled ? "text-primary" : ""}`} />
              <span>{whenText(s.after_hours)} · {s.label}</span>
              {!s.enabled && <span>· off</span>}
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <div className="rounded-xl border border-slate-200 divide-y divide-slate-100">
          {p.steps.map((s) => {
            const d = drafts[s.index];
            return (
              <div key={s.index} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
                <Switch size="sm" on={d.enabled} label={s.label} onChange={(v) => setDraft(s.index, { enabled: v })} />
                <span className={`flex-1 min-w-[150px] text-xs ${d.enabled ? "text-ink-primary font-medium" : "text-ink-tertiary"}`}>
                  {s.label}<span className="text-ink-tertiary font-normal"> · {s.action === "task" ? "call task" : s.action === "email" ? "email" : "text"}</span>
                </span>
                <span className="flex items-center gap-1.5 text-xs text-ink-secondary">
                  After
                  <input type="number" min={1} inputMode="numeric" value={d.value} aria-label={`Delay for ${s.label}`} disabled={!d.enabled}
                    onChange={(e) => setDraft(s.index, { value: e.target.value })}
                    className="glass-input w-16 rounded-lg px-2 py-1.5 text-xs tabular-nums text-right" />
                  <TeklySelect value={d.unit} aria-label={`Unit for ${s.label}`} disabled={!d.enabled}
                    onChange={(e) => setDraft(s.index, { unit: e.target.value as Draft["unit"] })}
                    className="glass-input rounded-lg px-2 py-1.5 text-xs">
                    <option value="hours">hours</option>
                    <option value="days">days</option>
                  </TeklySelect>
                </span>
              </div>
            );
          })}
          <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
            <Button variant="primary" size="sm" onClick={saveSteps} busy={busy === "steps"} disabled={!dirty}>{busy === "steps" ? "Saving…" : "Save steps"}</Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
            <span className="text-2xs text-ink-tertiary">Delays count from when the {quote ? "quote" : "job"} starts waiting.</span>
          </div>
        </div>
      )}

      {!editing && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" icon={<M n="edit" c="text-xs" />} onClick={startEdit} disabled={locked}>Change steps</Button>
          {!p.enabled && preview === null && (
            <Button variant="ghost" size="sm" icon={<M n="visibility" c="text-xs" />} onClick={openPreview} disabled={locked}>Preview</Button>
          )}
        </div>
      )}

      {preview === "loading" && <p className="text-xs text-ink-tertiary">Checking which {noun(2)} this would affect today…</p>}
      {pv && (
        <section aria-label="Preview before you switch on" className="rounded-xl border border-primary/20 bg-blue-50/60 p-3.5 space-y-2.5">
          <h4 className="text-xs font-semibold text-primary flex items-center gap-1.5"><M n="visibility" c="text-sm" />Preview before you switch on</h4>
          <p className="text-xs text-ink-secondary">
            Right now this would affect <b className="font-semibold text-ink-primary">{pv.count} {noun(pv.count)}</b>
            {pv.count > 0 && <> ({moneyIn(pv.total_amount, pv.currency)} {quote ? "quoted" : "waiting"})</>}.
            {pv.messages_today > 0 && <> {pv.messages_today} {pv.messages_today === 1 ? "message" : "messages"} would go out today.</>}
            {" "}No customer gets a message until you press Turn on.
          </p>
          {pv.jobs.length > 0 && (
            <div>
              <Button variant="link" onClick={() => setShowJobs((v) => !v)} aria-expanded={showJobs}>
                {showJobs ? "Hide the list" : `See the ${pv.count} ${noun(pv.count)}`}
              </Button>
              {showJobs && (
                <ul className="mt-2 rounded-lg border border-slate-200 bg-white divide-y divide-slate-100">
                  {pv.jobs.map((j) => (
                    <li key={j.order_id} className="flex items-center gap-3 px-3 py-2 text-xs">
                      <span className="font-semibold text-ink-primary tabular-nums">#{j.order_number}</span>
                      <span className="flex-1 min-w-0 truncate text-ink-secondary">{j.client_name}</span>
                      <span className="text-ink-tertiary tabular-nums whitespace-nowrap">{j.days_waiting} {j.days_waiting === 1 ? "day" : "days"} waiting</span>
                      <span className="w-20 text-right font-medium text-ink-primary tabular-nums">{moneyIn(j.amount, pv.currency)}</span>
                    </li>
                  ))}
                  {pv.count > pv.jobs.length && <li className="px-3 py-2 text-2xs text-ink-tertiary">And {pv.count - pv.jobs.length} more</li>}
                </ul>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" onClick={turnOn} busy={busy === "toggle"} disabled={locked}>{busy === "toggle" ? "Turning on…" : "Turn on"}</Button>
            <Button variant="ghost" size="sm" onClick={() => setPreview(null)} disabled={busy === "toggle"}>Not now</Button>
          </div>
        </section>
      )}

      {err && <p role="alert" className="text-xs text-rose-600">{err}</p>}
    </section>
  );
}
