// D1/D2 — Calls & telephony settings card (Settings → Integrations).
// Provider (simulated or the shop's own Twilio account), the Twilio connection with a
// "Test connection" check and a Test-mode badge, the webhook URLs to paste into Twilio,
// the contacts-from-calls automation, and a simulated inbound call to try it all out.
// Twilio keys are write-only: the API never sends them back.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import { btnGhost, btnPrimary, labelCls } from "../invoices/billing";
import Switch from "../../components/Switch";
import SaveFooter from "../../components/SaveFooter";
import ModePill from "./ModePill";
import MissedCallSettings from "./MissedCallSettings";
import { saveFailed } from "../../lib/notice";

const lbl = labelCls;
const field = "glass-input w-full rounded-xl px-3 py-2 text-sm text-ink-primary disabled:opacity-60";
const primary = btnPrimary;
const ghost = `${btnGhost} bg-white`;
const hint = "text-xs text-ink-tertiary leading-relaxed";
const heading = "text-sm font-semibold text-ink-primary";

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
  if (c.provider === "simulated") return { text: "Simulated", tone: "test" };
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
      <div className={lbl}>{label}</div>
      <div className="flex items-center gap-2 min-w-0">
        <code className="flex-1 min-w-0 truncate rounded-lg bg-slate-50 border border-slate-200 px-2.5 py-2 text-xs text-ink-secondary">{value}</code>
        <button type="button" aria-label={`Copy ${label}`} className="w-9 h-9 flex-shrink-0 rounded-lg border border-slate-200 bg-white grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-50" onClick={() => { navigator.clipboard?.writeText(value).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(saveFailed("copy it")); }}>
          <M n={done ? "check" : "content_copy"} c="text-sm" />
        </button>
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

  return (
    <section aria-labelledby="tel-title" className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="p-3.5 border-b border-slate-100 flex flex-wrap items-center gap-2">
        <h3 id="tel-title" className={`${heading} flex items-center gap-1.5`}><M n="call" c="text-sm text-ink-tertiary" />Calls & telephony</h3>
        {cfg && <ModePill testId="tel-badge" mode={MODE[badge.tone]}>{badge.text}</ModePill>}
      </div>

      {loadErr && <div className="p-4 space-y-2"><Note kind="err">{loadErr}</Note><button className={ghost} onClick={load}>Try again</button></div>}
      {!cfg && !loadErr && <div className="p-4 space-y-2" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="h-8 rounded-lg bg-slate-100 animate-pulse" />)}</div>}

      {cfg && auto && (
        <div className="divide-y divide-slate-100">
          {/* Provider */}
          <div className="p-4 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="tel-provider" className={lbl}>Provider</label>
                <select id="tel-provider" className={field} value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>
                  <option value="simulated">Simulated (no real calls)</option>
                  <option value="twilio">Twilio</option>
                  {(cfg.aircall?.available || form.provider === "aircall") && <option value="aircall">Aircall</option>}
                  {!["simulated", "twilio", "aircall"].includes(form.provider) && <option value={form.provider}>{form.provider[0].toUpperCase() + form.provider.slice(1)} (events through the call webhook)</option>}
                </select>
              </div>
              <div className="flex items-center gap-2.5 text-sm font-medium text-ink-primary sm:pt-6">
                <Switch label="Calling is on" on={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} />Calling is on
              </div>
              <div>
                <label htmlFor="tel-number" className={lbl}>{form.provider === "twilio" ? "Your Twilio phone number" : "Caller ID number"}</label>
                <input id="tel-number" className={field} inputMode="tel" value={form.caller_id_number} onChange={(e) => setForm({ ...form, caller_id_number: e.target.value })} placeholder={form.provider === "twilio" && tw?.test_mode ? tw.test_number : "+1 555 010 0100"} />
              </div>
              {form.provider === "twilio" && (
                <div>
                  <label htmlFor="tel-forward" className={lbl}>Ring incoming calls on</label>
                  <input id="tel-forward" className={field} inputMode="tel" value={form.forward_to} onChange={(e) => setForm({ ...form, forward_to: e.target.value })} placeholder="Front desk phone" />
                </div>
              )}
              <div className="sm:col-span-2">
                <label htmlFor="tel-allowed" className={lbl}>Countries staff can call</label>
                <input id="tel-allowed" className={field} value={form.allowed} onChange={(e) => setForm({ ...form, allowed: e.target.value })}
                  placeholder={cfg.destinations?.default.length ? `Your country only (${codes(cfg.destinations.default)})` : "Calling codes, e.g. +1, +44"} />
                <p className={`mt-1 ${hint}`}>Calling codes separated by commas. Leave empty to allow only your own country{cfg.destinations?.default.length ? ` (${codes(cfg.destinations.default)})` : ""}. This guards against toll fraud; in Twilio also limit Voice Geo Permissions to the same countries.</p>
              </div>
            </div>
            {form.provider === "twilio" && <p className={hint}>Click-to-call rings the phone number on your own profile first, then connects the customer. With no number to ring, callers hear that you'll call them back.</p>}
            {msg?.where === "config" && msg.kind === "err" && note("config")}
            <SaveFooter className="!px-0 !pb-0 !pt-1" dirty={formDirty} busy={busy === "config"} onSave={saveConfig}
              onDiscard={() => { setForm(formSaved); setMsg(null); }} msg={msg?.where === "config" && msg.kind === "ok" ? { ok: true, text: msg.text } : null} />
          </div>

          {/* Twilio account */}
          {form.provider === "twilio" && tw && (
            <div className="p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className={heading}>Twilio account</span>
                {tw.connected && <ModePill mode={tw.test_mode ? "test" : "live"} />}
              </div>
              {tw.connected ? (
                <>
                  <p className="text-xs text-ink-secondary">{tw.test_mode ? `Connected with test credentials: nothing is dialled or billed. Use ${tw.test_number} as your number.` : "Connected with live credentials."}</p>
                  <div className="flex flex-wrap gap-2">
                    <button className={primary} disabled={busy === "twilio"} onClick={testConn}>Test connection</button>
                    <button className={ghost} disabled={busy === "twilio"} onClick={disconnect}>Disconnect</button>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-xs text-ink-secondary">{tw.live_allowed ? "Paste your Account SID and auth token from the Twilio console." : "Use your Twilio test credentials (Twilio console → API keys & tokens → Test credentials). Live credentials are turned off on this server."}</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div><label htmlFor="tw-sid" className={lbl}>Account SID</label><input id="tw-sid" className={field} autoComplete="off" value={creds.account_sid} onChange={(e) => setCreds({ ...creds, account_sid: e.target.value })} placeholder="AC…" /></div>
                    <div><label htmlFor="tw-token" className={lbl}>Auth token</label><input id="tw-token" type="password" className={field} autoComplete="new-password" value={creds.auth_token} onChange={(e) => setCreds({ ...creds, auth_token: e.target.value })} /></div>
                  </div>
                  <div><button className={primary} disabled={busy === "twilio" || !creds.account_sid.trim() || !creds.auth_token.trim()} onClick={connect}>Connect Twilio</button></div>
                </>
              )}
              {note("twilio")}
              <div className="space-y-2 pt-1">
                <p className={hint}>In Twilio, open your phone number and paste these in its voice settings:</p>
                <CopyRow label="A call comes in (webhook, POST)" value={tw.voice_url} />
                <CopyRow label="Call status changes (POST)" value={tw.status_url} />
              </div>
            </div>
          )}

          {/* WS-C: Aircall (only when the server has it switched on) */}
          {form.provider === "aircall" && cfg.aircall && (
            <div className="p-4 space-y-3">
              <div className={heading}>Aircall account</div>
              {!cfg.aircall.available ? <p className={hint}>Aircall isn't switched on for this server yet.</p> : (
                <>
                  <p className={hint}>In Aircall, open Integrations → Webhook, add the address below, and paste the webhook's token here. Tekly checks the token on every call event.</p>
                  <CopyRow label="Aircall webhook address" value={cfg.aircall.webhook_url} />
                  {cfg.aircall.connected
                    ? <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-ink-secondary">Token saved.</span><button className={ghost} disabled={busy === "aircall"} onClick={removeAircall}>Remove token</button></div>
                    : <div className="flex flex-wrap items-end gap-2">
                        <div className="flex-1 min-w-[12rem]"><label htmlFor="ac-token" className={lbl}>Webhook token</label><input id="ac-token" type="password" autoComplete="off" className={field} value={acToken} onChange={(e) => setAcToken(e.target.value)} /></div>
                        <button className={primary} disabled={busy === "aircall" || acToken.trim().length < 16} onClick={saveAircall}>Save token</button>
                      </div>}
                </>
              )}
              {note("aircall")}
            </div>
          )}

          {/* WS-C: missed calls become inquiries */}
          <MissedCallSettings />

          {/* D2: contacts from calls */}
          <div className="p-4 space-y-3">
            <div className={`flex items-center gap-3 ${heading}`}>
              <Switch label="Create contacts from calls" on={auto.enabled} onChange={(v) => setAuto({ ...auto, enabled: v })} />Create contacts from calls
            </div>
            <p className={hint}>When an unknown number calls, Tekly adds a contact named after the number and marks it "Created from call". Known numbers are matched, never duplicated.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="auto-dir" className={lbl}>Which calls</label>
                <select id="auto-dir" className={field} value={auto.config.direction} disabled={!auto.enabled} onChange={(e) => setAuto({ ...auto, config: { ...auto.config, direction: e.target.value as "incoming" | "all" } })}>
                  <option value="incoming">Incoming calls only</option>
                  <option value="all">Incoming and outgoing calls</option>
                </select>
              </div>
              <div className="flex items-center gap-2.5 text-sm font-medium text-ink-primary sm:pt-6">
                <Switch label="Assign a manager" disabled={!auto.enabled} on={auto.config.assign_manager} onChange={(v) => setAuto({ ...auto, config: { ...auto.config, assign_manager: v } })} />Assign a manager
              </div>
            </div>
            {auto.config.assign_manager && auto.enabled && <p className={hint}>The manager is the person who placed the call, or else whoever owns the extension that was dialled.</p>}
            <div>
              <div className={lbl}>Tags for new contacts</div>
              {tags.length === 0
                ? <p className={hint}>No tags yet. Tags you create for contacts show up here.</p>
                : <div className="flex flex-wrap gap-1.5" role="group" aria-label="Tags for new contacts">
                    {tags.map((t) => {
                      const on = auto.config.tag_ids.includes(t.id);
                      return (
                        <button key={t.id} type="button" aria-pressed={on} disabled={!auto.enabled}
                          onClick={() => setAuto({ ...auto, config: { ...auto.config, tag_ids: on ? auto.config.tag_ids.filter((x) => x !== t.id) : [...auto.config.tag_ids, t.id] } })}
                          className={`px-2.5 py-1 rounded-full text-xs font-semibold border disabled:opacity-50 ${on ? "bg-primary text-white border-primary" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>
                          <span className="inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle" style={{ background: t.color }} />{t.name}
                        </button>
                      );
                    })}
                  </div>}
            </div>
            {msg?.where === "auto" && msg.kind === "err" && note("auto")}
            <SaveFooter className="!px-0 !pb-0 !pt-1" dirty={autoDirty} busy={busy === "auto"} onSave={() => saveAuto(auto)} saveLabel="Save automation"
              onDiscard={() => { setAuto(autoSaved); setMsg(null); }} msg={msg?.where === "auto" && msg.kind === "ok" ? { ok: true, text: msg.text } : null} />
          </div>

          {/* Try it: simulated inbound call */}
          {(savedProvider === null || savedProvider === "simulated") && (
            <div className="p-4 space-y-3">
              <div className={heading}>Try it</div>
              <p className={hint}>Simulate an incoming call. Nothing is dialled; the call shows up in the call log (Marketing → Call log).</p>
              <div className="flex items-center gap-2.5 text-sm font-medium text-ink-primary">
                <Switch size="sm" label="Nobody answers (a missed call)" on={sim.missed} onChange={(v) => setSim({ ...sim, missed: v })} />Nobody answers (a missed call)
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-[1fr_8rem_auto] gap-2 items-end">
                <div><label htmlFor="sim-from" className={lbl}>Caller's number</label><input id="sim-from" className={field} inputMode="tel" value={sim.from_number} onChange={(e) => setSim({ ...sim, from_number: e.target.value })} placeholder="+1 415 555 0100" /></div>
                <div><label htmlFor="sim-ext" className={lbl}>Extension</label><input id="sim-ext" className={field} value={sim.extension} onChange={(e) => setSim({ ...sim, extension: e.target.value })} placeholder="Optional" /></div>
                <button className={`${ghost} justify-center whitespace-nowrap h-[38px]`} disabled={busy === "sim" || !sim.from_number.trim()} onClick={simulate}><M n="call_received" c="text-sm" />Simulate call</button>
              </div>
              {note("sim")}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
