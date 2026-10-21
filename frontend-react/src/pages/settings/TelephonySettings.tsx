// D1/D2 — Calls & telephony settings card (Settings → Integrations).
// Provider (simulated or the shop's own Twilio account), the Twilio connection with a
// "Test connection" check and a Test-mode badge, the webhook URLs to paste into Twilio,
// the contacts-from-calls automation, and a simulated inbound call to try it all out.
// Twilio keys are write-only: the API never sends them back.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";

const lbl = "block text-[11px] font-bold text-ink-tertiary mb-1";
const field = "glass-input w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg";
const btn = "px-3 py-2 rounded-lg text-xs font-semibold disabled:opacity-60";
const primary = `${btn} bg-primary text-white hover:brightness-110`;
const ghost = `${btn} border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50`;

export type TwilioState = { connected: boolean; mode: "test" | "live" | null; test_mode: boolean; live_allowed: boolean; voice_url: string; status_url: string; test_number: string };
export type TelConfig = { provider: string; enabled: boolean; caller_id_number: string | null; forward_to: string | null; twilio: TwilioState };
export type ContactAuto = { enabled: boolean; config: { direction: "incoming" | "all"; tag_ids: string[]; assign_manager: boolean } };
type Tag = { id: string; name: string; color: string };

export function statusBadge(c: TelConfig | null): { text: string; tone: "on" | "off" | "test" } {
  if (!c || !c.enabled) return { text: "Off", tone: "off" };
  if (c.provider === "twilio") return c.twilio.test_mode ? { text: "Twilio · Test mode", tone: "test" } : { text: "Twilio · Live", tone: "on" };
  if (c.provider === "simulated") return { text: "Simulated", tone: "test" };
  return { text: c.provider, tone: "on" };
}

const TONE = { on: "bg-emerald-50 text-emerald-700 border-emerald-200", off: "bg-slate-100 text-ink-tertiary border-slate-200", test: "bg-amber-50 text-amber-800 border-amber-200" };

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
        <code className="flex-1 min-w-0 truncate rounded-lg bg-slate-50 border border-slate-200 px-2.5 py-2 text-[11px] text-ink-secondary">{value}</code>
        <button type="button" aria-label={`Copy ${label}`} className={ghost} onClick={() => { navigator.clipboard?.writeText(value).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(() => {}); }}>
          <M n={done ? "check" : "content_copy"} c="text-sm" />
        </button>
      </div>
    </div>
  );
}

export default function TelephonySettings() {
  const [cfg, setCfg] = useState<TelConfig | null>(null);
  const [auto, setAuto] = useState<ContactAuto | null>(null);
  const [tags, setTags] = useState<Tag[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const [form, setForm] = useState({ provider: "simulated", enabled: false, caller_id_number: "", forward_to: "" });
  const [creds, setCreds] = useState({ account_sid: "", auth_token: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string; where: string } | null>(null);
  const [sim, setSim] = useState({ from_number: "", extension: "" });

  function apply(c: TelConfig) {
    setCfg(c);
    setForm({ provider: c.provider, enabled: c.enabled, caller_id_number: c.caller_id_number || "", forward_to: c.forward_to || "" });
  }
  function load() {
    setLoadErr(null);
    Promise.all([api.get<TelConfig>("/telephony/config"), api.get<ContactAuto>("/telephony/automation/contacts"), api.get<{ items: Tag[] }>("/tags")])
      .then(([c, a, t]) => { apply(c); setAuto(a); setTags(t.items || []); })
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
    apply(await api.put<TelConfig>("/telephony/config", { provider: form.provider, enabled: form.enabled, caller_id_number: form.caller_id_number.trim() || null, forward_to: form.forward_to.trim() || null }));
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
    setAuto(await api.put<ContactAuto>("/telephony/automation/contacts", { enabled: next.enabled, ...next.config }));
    return "Saved.";
  });
  const simulate = () => run("sim", async () => {
    const r = await api.post<{ client_id: string | null; client_created: boolean; matched: boolean }>("/telephony/simulate/inbound", { from_number: sim.from_number.trim(), extension: sim.extension.trim() || null });
    return r.client_created ? "Call logged. A new contact was created from the call." : r.matched ? "Call logged and matched to an existing contact." : "Call logged. No contact was created (the automation is off).";
  });

  const note = (where: string) => (msg && msg.where === where ? <Note kind={msg.kind}>{msg.text}</Note> : null);
  const badge = statusBadge(cfg);
  const tw = cfg?.twilio;
  const savedProvider = cfg?.enabled ? cfg.provider : null;

  return (
    <section aria-labelledby="tel-title" className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="p-3.5 border-b border-slate-100 flex flex-wrap items-center justify-between gap-2">
        <h3 id="tel-title" className="text-xs font-bold text-ink-primary flex items-center gap-1.5"><M n="smartphone" c="text-sm" />Calls & telephony</h3>
        {cfg && <span data-testid="tel-badge" className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${TONE[badge.tone]}`}>{badge.text}</span>}
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
                  {form.provider !== "simulated" && form.provider !== "twilio" && <option value={form.provider}>{form.provider[0].toUpperCase() + form.provider.slice(1)} (events through the call webhook)</option>}
                </select>
              </div>
              <label className="flex items-center gap-2 text-xs font-semibold text-ink-primary sm:pt-5">
                <input type="checkbox" className="accent-primary" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />Calling is on
              </label>
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
            </div>
            {form.provider === "twilio" && <p className="text-[11px] text-ink-tertiary">Click-to-call rings the phone number on your own profile first, then connects the customer. With no number to ring, callers hear that you'll call them back.</p>}
            {note("config")}
            <div><button className={primary} disabled={busy === "config"} onClick={saveConfig}>Save</button></div>
          </div>

          {/* Twilio account */}
          {form.provider === "twilio" && tw && (
            <div className="p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-bold text-ink-primary">Twilio account</span>
                {tw.connected && tw.test_mode && <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-amber-50 text-amber-800 border-amber-200">Test mode</span>}
                {tw.connected && !tw.test_mode && <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-700 border-emerald-200">Live</span>}
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
                <p className="text-[11px] text-ink-tertiary">In Twilio, open your phone number and paste these in its voice settings:</p>
                <CopyRow label="A call comes in (webhook, POST)" value={tw.voice_url} />
                <CopyRow label="Call status changes (POST)" value={tw.status_url} />
              </div>
            </div>
          )}

          {/* D2: contacts from calls */}
          <div className="p-4 space-y-3">
            <label className="flex items-center gap-2 text-xs font-bold text-ink-primary">
              <input type="checkbox" className="accent-primary" checked={auto.enabled} onChange={(e) => setAuto({ ...auto, enabled: e.target.checked })} />Create contacts from calls
            </label>
            <p className="text-[11px] text-ink-tertiary">When an unknown number calls, Tekly adds a contact named after the number and marks it "Created from call". Known numbers are matched, never duplicated.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="auto-dir" className={lbl}>Which calls</label>
                <select id="auto-dir" className={field} value={auto.config.direction} disabled={!auto.enabled} onChange={(e) => setAuto({ ...auto, config: { ...auto.config, direction: e.target.value as "incoming" | "all" } })}>
                  <option value="incoming">Incoming calls only</option>
                  <option value="all">Incoming and outgoing calls</option>
                </select>
              </div>
              <label className="flex items-center gap-2 text-xs font-semibold text-ink-primary sm:pt-5">
                <input type="checkbox" className="accent-primary" disabled={!auto.enabled} checked={auto.config.assign_manager} onChange={(e) => setAuto({ ...auto, config: { ...auto.config, assign_manager: e.target.checked } })} />Assign a manager
              </label>
            </div>
            {auto.config.assign_manager && auto.enabled && <p className="text-[11px] text-ink-tertiary">The manager is the person who placed the call, or else whoever owns the extension that was dialled.</p>}
            <div>
              <div className={lbl}>Tags for new contacts</div>
              {tags.length === 0
                ? <p className="text-[11px] text-ink-tertiary">No tags yet. Tags you create for contacts show up here.</p>
                : <div className="flex flex-wrap gap-1.5" role="group" aria-label="Tags for new contacts">
                    {tags.map((t) => {
                      const on = auto.config.tag_ids.includes(t.id);
                      return (
                        <button key={t.id} type="button" aria-pressed={on} disabled={!auto.enabled}
                          onClick={() => setAuto({ ...auto, config: { ...auto.config, tag_ids: on ? auto.config.tag_ids.filter((x) => x !== t.id) : [...auto.config.tag_ids, t.id] } })}
                          className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border disabled:opacity-50 ${on ? "bg-primary text-white border-primary" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>
                          <span className="inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle" style={{ background: t.color }} />{t.name}
                        </button>
                      );
                    })}
                  </div>}
            </div>
            {note("auto")}
            <div><button className={primary} disabled={busy === "auto"} onClick={() => saveAuto(auto)}>Save automation</button></div>
          </div>

          {/* Try it: simulated inbound call */}
          {(savedProvider === null || savedProvider === "simulated") && (
            <div className="p-4 space-y-3">
              <div className="text-xs font-bold text-ink-primary">Try it</div>
              <p className="text-[11px] text-ink-tertiary">Simulate an incoming call. Nothing is dialled; the call shows up in the call log (Marketing → Call log).</p>
              <div className="grid grid-cols-1 sm:grid-cols-[1fr_8rem_auto] gap-2 items-end">
                <div><label htmlFor="sim-from" className={lbl}>Caller's number</label><input id="sim-from" className={field} inputMode="tel" value={sim.from_number} onChange={(e) => setSim({ ...sim, from_number: e.target.value })} placeholder="+1 415 555 0100" /></div>
                <div><label htmlFor="sim-ext" className={lbl}>Extension</label><input id="sim-ext" className={field} value={sim.extension} onChange={(e) => setSim({ ...sim, extension: e.target.value })} placeholder="Optional" /></div>
                <button className={`${ghost} flex items-center justify-center gap-1.5 whitespace-nowrap`} disabled={busy === "sim" || !sim.from_number.trim()} onClick={simulate}><M n="smartphone" c="text-sm" />Simulate call</button>
              </div>
              {note("sim")}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
