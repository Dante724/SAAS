// D1/D2 — Calls & telephony settings card (Settings → Integrations).
// Provider (simulated or the shop's own Twilio account), the Twilio connection with a
// "Test connection" check and a Test-mode badge, the webhook URLs to paste into Twilio,
// the contacts-from-calls automation, and a simulated inbound call to try it all out.
// Twilio keys are write-only: the API never sends them back.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import Button, { IconButton } from "../../components/Button";
import { Input, Select, Field } from "../../components/Field";
import Section from "../../components/Section";
import Switch from "../../components/Switch";
import SaveFooter from "../../components/SaveFooter";
import { SettingRow } from "../../components/SettingRow";
import { ErrorState } from "../../components/EmptyState";
import ModePill from "./ModePill";
import MissedCallSettings from "./MissedCallSettings";
import { sectionFooter, rowField } from "./kit";
import { saveFailed } from "../../lib/notice";

const hint = "text-xs text-ink-tertiary leading-relaxed";

export type TwilioState = { connected: boolean; mode: "test" | "live" | null; test_mode: boolean; live_allowed: boolean; voice_url: string; status_url: string; test_number: string };
export type Destinations = { default: string[]; override: string[] | null; effective: string[] };
export type AircallState = { available: boolean; connected: boolean; webhook_url: string };
export type TelConfig = { provider: string; enabled: boolean; caller_id_number: string | null; forward_to: string | null; destinations?: Destinations; twilio: TwilioState; aircall?: AircallState };
const codes = (list: string[] | null | undefined) => (list || []).map((p) => `+${p}`).join(", ");
export type ContactAuto = { enabled: boolean; config: { direction: "incoming" | "all"; tag_ids: string[]; assign_manager: boolean } };
type Tag = { id: string; name: string; color: string };

export function statusBadge(c: TelConfig | null): { text: string; tone: "on" | "off" | "test" } {
  if (!c || !c.enabled) return { text: "Off", tone: "off" };
  if (c.provider === "twilio") return c.twilio.test_mode ? { text: "Twilio · Test mode", tone: "test" } : { text: "Twilio · Live", tone: "on" };
  if (c.provider === "simulated") return { text: "Practice mode", tone: "test" };
  if (c.provider === "aircall") return { text: "Aircall", tone: "on" };
  return { text: c.provider, tone: "on" };
}

const MODE = { on: "live", off: "off", test: "test" } as const;

function Note({ kind, children }: { kind: "ok" | "err" | "info"; children: React.ReactNode }) {
  const c = kind === "ok" ? "bg-emerald-50 border-emerald-200 text-emerald-800" : kind === "err" ? "bg-rose-50 border-rose-200 text-rose-700" : "bg-slate-50 border-slate-200 text-ink-secondary";
  return <div role={kind === "err" ? "alert" : "status"} className={`rounded-lg border px-3 py-2 text-xs ${c}`}>{children}</div>;
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false);
  return (
    <div>
      <div className="block text-xs font-semibold text-ink-secondary mb-1.5">{label}</div>
      <div className="flex items-center gap-2 min-w-0">
        <code className="flex-1 min-w-0 truncate rounded-xl bg-slate-50 border border-slate-200 px-3 h-9 leading-9 text-xs text-ink-secondary">{value}</code>
        <IconButton variant="secondary" label={`Copy ${label}`} icon={<M n={done ? "check" : "content_copy"} c="text-sm" />}
          onClick={() => { navigator.clipboard?.writeText(value).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(saveFailed("copy it")); }} />
      </div>
    </div>
  );
}

export default function TelephonySettings() {
  const [cfg, setCfg] = useState<TelConfig | null>(null);
  const [auto, setAuto] = useState<ContactAuto | null>(null);
  const [autoSaved, setAutoSaved] = useState<ContactAuto | null>(null);
  const [tags, setTags] = useState<Tag[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const blank = { provider: "simulated", enabled: false, caller_id_number: "", forward_to: "", allowed: "" };
  const [form, setForm] = useState(blank);
  const [formSaved, setFormSaved] = useState(blank);
  const [creds, setCreds] = useState({ account_sid: "", auth_token: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string; where: string } | null>(null);
  const [sim, setSim] = useState({ from_number: "", extension: "", missed: false });
  const [acToken, setAcToken] = useState("");

  function apply(c: TelConfig) {
    setCfg(c);
    const f = { provider: c.provider, enabled: c.enabled, caller_id_number: c.caller_id_number || "", forward_to: c.forward_to || "", allowed: codes(c.destinations?.override) };
    setForm(f); setFormSaved(f);
  }
  function load() {
    setLoadErr(null);
    Promise.all([api.get<TelConfig>("/telephony/config"), api.get<ContactAuto>("/telephony/automation/contacts"), api.get<{ items: Tag[] }>("/tags")])
      .then(([c, a, t]) => { apply(c); setAuto(a); setAutoSaved(a); setTags(t.items || []); })
      .catch((e) => setLoadErr(e?.message || "Couldn't load telephony settings"));
  }
  useEffect(load, []);

  async function run(where: string, fn: () => Promise<string | void>) {
    setBusy(where); setMsg(null);
    try { const ok = await fn(); if (ok) setMsg({ kind: "ok", text: ok, where }); }
    catch (e: any) { setMsg({ kind: "err", text: e?.message || "Something went wrong", where }); }
    finally { setBusy(null); }
  }

  const saveConfig = () => run("config", async () => {
    apply(await api.put<TelConfig>("/telephony/config", { provider: form.provider, enabled: form.enabled, caller_id_number: form.caller_id_number.trim() || null, forward_to: form.forward_to.trim() || null, allowed_prefixes: form.allowed.split(/[,\s]+/).filter(Boolean) }));
    return "Saved.";
  });
  const connect = () => run("twilio", async () => {
    const r = await api.put<TwilioState & { account_name?: string }>("/telephony/twilio", { account_sid: creds.account_sid.trim(), auth_token: creds.auth_token.trim() });
    setCreds({ account_sid: "", auth_token: "" });
    setCfg((c) => (c ? { ...c, twilio: r } : c));
    return r.test_mode ? "Connected with test credentials. Calls use Twilio's test numbers and nothing is dialled." : "Connected.";
  });
  const testConn = () => run("twilio", async () => {
    const r = await api.post<{ ok: boolean; mode: string; account_name?: string | null }>("/telephony/twilio/test");
    return r.mode === "test" ? "Connection works. These are test credentials." : `Connection works${r.account_name ? ` (${r.account_name})` : ""}.`;
  });
  const disconnect = async () => {
    if (!(await confirmDialog("Disconnect Twilio? Calls through Twilio stop until you connect again.", { danger: true, confirmLabel: "Disconnect" }))) return;
    run("twilio", async () => { apply(await api.del<TelConfig>("/telephony/twilio")); return "Disconnected."; });
  };
  const saveAuto = (next: ContactAuto) => run("auto", async () => {
    const r = await api.put<ContactAuto>("/telephony/automation/contacts", { enabled: next.enabled, ...next.config });
    setAuto(r); setAutoSaved(r);
    return "Saved.";
  });
  const saveAircall = () => run("aircall", async () => {
    const r = await api.put<AircallState>("/telephony/aircall", { webhook_token: acToken.trim() });
    setAcToken("");
    setCfg((c) => (c ? { ...c, aircall: r } : c));
    return "Aircall token saved. Turn calling on with Aircall as the provider.";
  });
  const removeAircall = async () => {
    if (!(await confirmDialog("Remove the Aircall token? Aircall events stop until you save it again.", { danger: true, confirmLabel: "Remove" }))) return;
    run("aircall", async () => { const r = await api.del<AircallState>("/telephony/aircall"); setCfg((c) => (c ? { ...c, aircall: r } : c)); return "Removed."; });
  };
  const simulate = () => run("sim", async () => {
    const r = await api.post<{ client_id: string | null; client_created: boolean; matched: boolean; lead_id?: string | null }>("/telephony/simulate/inbound", { from_number: sim.from_number.trim(), extension: sim.extension.trim() || null, missed: sim.missed });
    if (r.lead_id) return "Missed call logged. It's in Inquiries with a call-back task.";
    return r.client_created ? "Call logged. A new contact was created from the call." : r.matched ? "Call logged and matched to an existing contact." : "Call logged. No contact was created (the automation is off).";
  });

  const note = (where: string) => (msg && msg.where === where ? <Note kind={msg.kind}>{msg.text}</Note> : null);
  const badge = statusBadge(cfg);
  const tw = cfg?.twilio;
  const savedProvider = cfg?.enabled ? cfg.provider : null;
  const formDirty = JSON.stringify(form) !== JSON.stringify(formSaved);
  const autoDirty = JSON.stringify(auto) !== JSON.stringify(autoSaved);

  const telIcon = "call";
  const header = { title: "Calls & telephony", icon: telIcon, meta: cfg ? <ModePill testId="tel-badge" mode={MODE[badge.tone]}>{badge.text}</ModePill> : null };

  if (loadErr) return (
    <Section {...header}>
      <ErrorState title="Telephony settings didn't load" body={loadErr} onRetry={load} />
    </Section>
  );
  if (!cfg || !auto) return (
    <Section {...header}>
      <div className="space-y-2" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="h-9 rounded-xl bg-slate-100 animate-pulse" />)}</div>
    </Section>
  );

  return (
    <>
      {/* Provider and calling */}
      <Section {...header} description="Who carries your calls, the number customers see, and where staff may call.">
        <div className="divide-y divide-slate-100">
          <SettingRow label="Calling" help={form.enabled ? "On: staff can call customers and incoming calls are logged." : "Off: no calls are made or logged."}>
            <Switch label="Calling" on={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} />
          </SettingRow>
          <div className="py-4 last:pb-0 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Provider" htmlFor="tel-provider">
              <Select id="tel-provider" value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>
                <option value="simulated">Practice mode (no real calls)</option>
                <option value="twilio">Twilio</option>
                {(cfg.aircall?.available || form.provider === "aircall") && <option value="aircall">Aircall</option>}
                {!["simulated", "twilio", "aircall"].includes(form.provider) && <option value={form.provider}>{form.provider[0].toUpperCase() + form.provider.slice(1)} (events through the call webhook)</option>}
              </Select>
            </Field>
            <Field label={form.provider === "twilio" ? "Your Twilio phone number" : "Caller ID number"} htmlFor="tel-number">
              <Input id="tel-number" inputMode="tel" value={form.caller_id_number} onChange={(e) => setForm({ ...form, caller_id_number: e.target.value })} placeholder={form.provider === "twilio" && tw?.test_mode ? tw.test_number : "+1 555 010 0100"} />
            </Field>
            {form.provider === "twilio" && (
              <Field label="Ring incoming calls on" htmlFor="tel-forward">
                <Input id="tel-forward" inputMode="tel" value={form.forward_to} onChange={(e) => setForm({ ...form, forward_to: e.target.value })} placeholder="Front desk phone" />
              </Field>
            )}
            <Field className="sm:col-span-2" label="Countries staff can call" htmlFor="tel-allowed"
              hint={`Calling codes separated by commas. Leave empty to allow only your own country${cfg.destinations?.default.length ? ` (${codes(cfg.destinations.default)})` : ""}. This guards against toll fraud; in Twilio also limit Voice Geo Permissions to the same countries.`}>
              <Input id="tel-allowed" value={form.allowed} onChange={(e) => setForm({ ...form, allowed: e.target.value })}
                placeholder={cfg.destinations?.default.length ? `Your country only (${codes(cfg.destinations.default)})` : "Calling codes, e.g. +1, +44"} />
            </Field>
            {form.provider === "twilio" && <p className={`sm:col-span-2 ${hint}`}>Click-to-call rings the phone number on your own profile first, then connects the customer. With no number to ring, callers hear that you'll call them back.</p>}
          </div>
        </div>
        {msg?.where === "config" && msg.kind === "err" && <div className="mt-3">{note("config")}</div>}
        <SaveFooter className={sectionFooter} dirty={formDirty} busy={busy === "config"} onSave={saveConfig}
          onDiscard={() => { setForm(formSaved); setMsg(null); }} msg={msg?.where === "config" && msg.kind === "ok" ? { ok: true, text: msg.text } : null} />
      </Section>

      {/* Twilio account */}
      {form.provider === "twilio" && tw && (
        <Section title="Twilio account" icon="key" meta={tw.connected ? <ModePill mode={tw.test_mode ? "test" : "live"} /> : null}
          description={tw.connected
            ? (tw.test_mode ? `Connected with test credentials: nothing is dialled or billed. Use ${tw.test_number} as your number.` : "Connected with live credentials.")
            : (tw.live_allowed ? "Paste your Account SID and auth token from the Twilio console." : "Use your Twilio test credentials (Twilio console → API keys & tokens → Test credentials). Live credentials are turned off on this server.")}
          actions={tw.connected ? <>
            <Button variant="primary" disabled={busy === "twilio"} onClick={testConn}>Test connection</Button>
            <Button disabled={busy === "twilio"} onClick={disconnect}>Disconnect</Button>
          </> : undefined}>
          <div className="space-y-3">
            {!tw.connected && (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Account SID" htmlFor="tw-sid"><Input id="tw-sid" autoComplete="off" value={creds.account_sid} onChange={(e) => setCreds({ ...creds, account_sid: e.target.value })} placeholder="AC…" /></Field>
                  <Field label="Auth token" htmlFor="tw-token"><Input id="tw-token" type="password" autoComplete="new-password" value={creds.auth_token} onChange={(e) => setCreds({ ...creds, auth_token: e.target.value })} /></Field>
                </div>
                <div><Button variant="primary" disabled={busy === "twilio" || !creds.account_sid.trim() || !creds.auth_token.trim()} onClick={connect}>Connect Twilio</Button></div>
              </>
            )}
            {note("twilio")}
            <div className="space-y-2">
              <p className={hint}>In Twilio, open your phone number and paste these in its voice settings:</p>
              <CopyRow label="A call comes in (webhook, POST)" value={tw.voice_url} />
              <CopyRow label="Call status changes (POST)" value={tw.status_url} />
            </div>
          </div>
        </Section>
      )}

      {/* WS-C: Aircall (only when the server has it switched on) */}
      {form.provider === "aircall" && cfg.aircall && (
        <Section title="Aircall account" icon="key"
          description={cfg.aircall.available ? "In Aircall, open Integrations → Webhook, add the address below, and paste the webhook's token here. Tekly checks the token on every call event." : "Aircall isn't switched on for this server yet."}>
          {cfg.aircall.available && (
            <div className="space-y-3">
              <CopyRow label="Aircall webhook address" value={cfg.aircall.webhook_url} />
              {cfg.aircall.connected
                ? <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-ink-secondary">Token saved.</span><Button disabled={busy === "aircall"} onClick={removeAircall}>Remove token</Button></div>
                : <div className="flex flex-wrap items-end gap-2">
                    <Field className="flex-1 min-w-[12rem]" label="Webhook token" htmlFor="ac-token"><Input id="ac-token" type="password" autoComplete="off" value={acToken} onChange={(e) => setAcToken(e.target.value)} /></Field>
                    <Button variant="primary" disabled={busy === "aircall" || acToken.trim().length < 16} onClick={saveAircall}>Save token</Button>
                  </div>}
              {note("aircall")}
            </div>
          )}
        </Section>
      )}

      {/* WS-C: missed calls become inquiries */}
      <MissedCallSettings />

      {/* D2: contacts from calls */}
      <Section title="Contacts from calls" icon="person_add"
        description={'When an unknown number calls, Tekly adds a contact named after the number and marks it "Created from call". Known numbers are matched, never duplicated.'}>
        <div className="divide-y divide-slate-100">
          <SettingRow label="Create contacts from calls">
            <Switch label="Create contacts from calls" on={auto.enabled} onChange={(v) => setAuto({ ...auto, enabled: v })} />
          </SettingRow>
          <SettingRow label="Which calls" htmlFor="auto-dir" dim={!auto.enabled} stack>
            <Select id="auto-dir" className={rowField} value={auto.config.direction} disabled={!auto.enabled} onChange={(e) => setAuto({ ...auto, config: { ...auto.config, direction: e.target.value as "incoming" | "all" } })}>
              <option value="incoming">Incoming calls only</option>
              <option value="all">Incoming and outgoing calls</option>
            </Select>
          </SettingRow>
          <SettingRow label="Assign a manager" dim={!auto.enabled}
            help={!auto.enabled ? "Turn on Create contacts from calls first." : "The manager is the person who placed the call, or else whoever owns the extension that was dialled."}>
            <Switch label="Assign a manager" disabled={!auto.enabled} on={auto.config.assign_manager} onChange={(v) => setAuto({ ...auto, config: { ...auto.config, assign_manager: v } })} />
          </SettingRow>
          <div className={`py-4 last:pb-0 ${auto.enabled ? "" : "opacity-60"}`}>
            <div className="block text-sm font-medium text-ink-primary">Tags for new contacts</div>
            {tags.length === 0
              ? <p className={`mt-0.5 ${hint}`}>No tags yet. Tags you create for contacts show up here.</p>
              : <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Tags for new contacts">
                  {tags.map((t) => {
                    const on = auto.config.tag_ids.includes(t.id);
                    return (
                      <button key={t.id} type="button" aria-pressed={on} disabled={!auto.enabled}
                        onClick={() => setAuto({ ...auto, config: { ...auto.config, tag_ids: on ? auto.config.tag_ids.filter((x) => x !== t.id) : [...auto.config.tag_ids, t.id] } })}
                        className={`hit44 h-8 px-3 inline-flex items-center gap-1.5 rounded-full ring-1 ring-inset text-xs font-semibold transition-colors disabled:cursor-not-allowed ${on ? "bg-primary/10 text-primary ring-primary/20" : "bg-white text-ink-secondary ring-slate-200 hover:text-ink-primary hover:bg-slate-50"}`}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: t.color }} aria-hidden="true" />{t.name}
                      </button>
                    );
                  })}
                </div>}
          </div>
        </div>
        {msg?.where === "auto" && msg.kind === "err" && <div className="mt-3">{note("auto")}</div>}
        <SaveFooter className={sectionFooter} dirty={autoDirty} busy={busy === "auto"} onSave={() => saveAuto(auto)} saveLabel="Save automation"
          onDiscard={() => { setAuto(autoSaved); setMsg(null); }} msg={msg?.where === "auto" && msg.kind === "ok" ? { ok: true, text: msg.text } : null} />
      </Section>

      {/* Try it: practice inbound call */}
      {(savedProvider === null || savedProvider === "simulated") && (
        <Section title="Try it" icon="call_received" description="Practise an incoming call. Nothing is dialled; the call shows up in the call log (Marketing → Call log).">
          <div className="divide-y divide-slate-100">
            <SettingRow label="Nobody answers (a missed call)">
              <Switch label="Nobody answers (a missed call)" on={sim.missed} onChange={(v) => setSim({ ...sim, missed: v })} />
            </SettingRow>
            <div className="pt-4 grid grid-cols-1 sm:grid-cols-[1fr_8rem_auto] gap-3 items-end">
              <Field label="Caller's number" htmlFor="sim-from"><Input id="sim-from" inputMode="tel" value={sim.from_number} onChange={(e) => setSim({ ...sim, from_number: e.target.value })} placeholder="+1 415 555 0100" /></Field>
              <Field label="Extension" htmlFor="sim-ext"><Input id="sim-ext" value={sim.extension} onChange={(e) => setSim({ ...sim, extension: e.target.value })} placeholder="Optional" /></Field>
              <Button disabled={busy === "sim" || !sim.from_number.trim()} onClick={simulate} icon={<M n="call_received" c="text-sm" />}>Simulate call</Button>
            </div>
          </div>
          {msg?.where === "sim" && <div className="mt-3">{note("sim")}</div>}
        </Section>
      )}
    </>
  );
}
