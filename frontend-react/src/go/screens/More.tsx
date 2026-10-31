// More: who's signed in, inquiries, which Today to show, payment PIN / lock, appearance, changes waiting to sync, sign out.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Bell, BellOff, Check, ChevronRight, CloudOff, ExternalLink, KeyRound, Lock, LogOut, Monitor, Moon, PhoneIncoming, RefreshCw, ScanFace, Sun } from "lucide-react";
import { useMe } from "../../context/Me";
import { Card, Page, Section } from "../components/ui";
import { goQueue, type Conflict } from "../lib/offlineQueue";
import { inferRole, ROLE_LABEL, savedRole, saveRole, type GoRole } from "../lib/role";
import { useQueueStatus } from "../lib/useGo";
import { getTheme, setTheme, type ThemePref } from "../theme";
import { signOut } from "../session";
import { disablePush, enablePush, pushState, type PushState } from "../lib/push";
import { useLock } from "../components/Lock";
import { passkeyEnroll, passkeyOff, passkeyOn, passkeySupported } from "../lib/passkey";
import { useToast } from "../lib/useGo";

function Choice<T extends string>({ value, current, onPick, children }: { value: T; current: T; onPick: (v: T) => void; children: React.ReactNode }) {
  const on = value === current;
  return (
    <button onClick={() => onPick(value)} aria-pressed={on}
      className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left text-go-body text-ink-primary active:bg-slate-50 first:rounded-t-xl last:rounded-b-xl">
      <span className="flex-1 inline-flex items-center gap-3">{children}</span>
      {on && <Check size={20} className="text-primary" />}
    </button>
  );
}

const labelFor = (c: Conflict) => /\/status$/.test(c.url) ? "Status change" : `${c.method} ${c.url.replace(/^\/api\//, "")}`;

export default function More() {
  const { me } = useMe();
  const q = useQueueStatus();
  const [role, setRole] = useState<GoRole | "auto">(savedRole() ?? "auto");
  const [theme, setThemeState] = useState<ThemePref>(getTheme());
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  useEffect(() => { goQueue().conflicts().then(setConflicts).catch(() => { /* silent: offline queue storage may be unavailable */ }); }, [q.conflicts]);
  const [push, setPush] = useState<PushState | "busy" | null>(null);
  useEffect(() => { pushState().then(setPush).catch(() => setPush("unsupported")); }, []);
  async function togglePush() {
    const was = push;
    setPush("busy");
    try { setPush(was === "on" ? await disablePush() : await enablePush()); }
    catch { setPush(was); }
  }
  const pushNote: Record<string, string> = {
    unsupported: "On iPhone, add Tekly Go to your Home Screen first (Share → Add to Home Screen), then open it from there.",
    "not-configured": "Your shop hasn't set up notifications yet.",
    denied: "Notifications are blocked. Allow them for Tekly in your phone's Settings.",
  };

  const lock = useLock();
  const toast = useToast();
  const [faceOk, setFaceOk] = useState(false);
  const [face, setFace] = useState(passkeyOn(me?.id));
  useEffect(() => { passkeySupported().then(setFaceOk); }, []);
  useEffect(() => { setFace(passkeyOn(me?.id)); }, [me?.id]);
  async function toggleFace() {
    if (face) { passkeyOff(me?.id); setFace(false); return; }
    if (!(await lock.askPin("Turn on Face ID / Touch ID"))) return;
    try {
      if (me && await passkeyEnroll(me)) { setFace(true); toast({ title: "Face ID / Touch ID is on for this phone" }); }
    } catch { toast({ title: "Couldn't turn it on", body: "Your phone said no. Your PIN still works.", tone: "warn" }); }
  }

  const pickRole = (r: GoRole | "auto") => { setRole(r); saveRole(r === "auto" ? null : r); };
  const pickTheme = (t: ThemePref) => { setThemeState(t); setTheme(t); };

  return (
    <Page title="More">
      <Card className="p-4 mb-6 flex items-center gap-3">
        <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-white text-go-body font-bold">
          {(me?.name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase()}
        </span>
        <div className="min-w-0">
          <p className="font-semibold text-ink-primary truncate">{me?.name || "—"}</p>
          <p className="text-go-sub text-ink-secondary truncate">{me?.email}{me?.role ? ` · ${me.role}` : ""}</p>
        </div>
      </Card>

      <Card className="mb-6">
        <Link to="/inquiries" className="min-h-[3rem] flex items-center gap-3 px-4 text-go-body text-ink-primary active:bg-slate-50 rounded-xl">
          <PhoneIncoming size={18} className="text-ink-tertiary" /><span className="flex-1">Inquiries</span><ChevronRight size={18} className="text-ink-tertiary" />
        </Link>
      </Card>

      {(q.pending > 0 || q.conflicts > 0 || !q.online) && (
        <Section title="Sync">
          <Card className="p-4 space-y-3">
            <div className="flex items-center gap-3">
              <CloudOff size={20} className={q.held || !q.online ? "text-amber-700" : "text-ink-tertiary"} />
              <p className="flex-1 text-go-body text-ink-primary">
                {!q.online ? "You're offline. " : ""}
                {q.pending ? `${q.pending} change${q.pending > 1 ? "s" : ""} waiting to send.` : "Nothing waiting to send."}
                {q.held && <span className="block text-go-sub text-amber-800">This shop only accepts changes from its own network. They'll send on shop Wi-Fi.</span>}
              </p>
              {q.pending > 0 && q.online && (
                <button onClick={() => goQueue().flush()} aria-label="Send now" className="h-11 w-11 inline-flex items-center justify-center rounded-full text-primary active:bg-slate-100">
                  <RefreshCw size={20} className={q.syncing ? "animate-spin" : ""} />
                </button>
              )}
            </div>
            {conflicts.map((c) => (
              <div key={c.id} className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                <p className="flex items-center gap-2 font-semibold text-amber-800"><AlertTriangle size={16} />{labelFor(c)} didn't go through</p>
                <p className="mt-0.5 text-go-sub text-amber-800">{c.detail}</p>
                <div className="mt-2 flex gap-2">
                  <button onClick={() => goQueue().resolveConflict(c.id, "discard")} className="min-h-[2.75rem] flex-1 rounded-xl border border-amber-300 bg-white font-semibold text-ink-primary">Discard</button>
                  <button onClick={() => goQueue().resolveConflict(c.id, "retry")} className="min-h-[2.75rem] flex-1 rounded-xl bg-primary font-semibold text-white">Try again</button>
                </div>
              </div>
            ))}
          </Card>
        </Section>
      )}

      <Section title="Today shows">
        <Card className="divide-y-[0.5px] divide-slate-200">
          <Choice value="auto" current={role} onPick={pickRole}>Automatic <span className="text-ink-tertiary text-go-sub">({ROLE_LABEL[inferRole(me)]})</span></Choice>
          {(["tech", "desk", "owner"] as GoRole[]).map((r) => <Choice key={r} value={r} current={role} onPick={pickRole}>{ROLE_LABEL[r]}</Choice>)}
        </Card>
      </Section>

      <Section title="Payment PIN">
        <Card className="divide-y-[0.5px] divide-slate-200">
          <button onClick={() => lock.setupPin()} className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left text-go-body text-ink-primary active:bg-slate-50 first:rounded-t-xl last:rounded-b-xl">
            <KeyRound size={18} className={lock.pinSet ? "text-primary" : "text-ink-tertiary"} />
            <span className="flex-1">{lock.pinSet ? "PIN is set" : "Set up a PIN"}</span>
            <span className="text-go-sub font-semibold text-primary">{lock.pinSet ? "Change" : "Set up"}</span>
          </button>
          {lock.pinSet && faceOk && (
            <button onClick={toggleFace} aria-pressed={face} className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left text-go-body text-ink-primary active:bg-slate-50 first:rounded-t-xl last:rounded-b-xl">
              <ScanFace size={18} className={face ? "text-primary" : "text-ink-tertiary"} />
              <span className="flex-1">Face ID / Touch ID on this phone</span>
              <span className={`text-go-sub font-semibold ${face ? "text-primary" : "text-ink-tertiary"}`}>{face ? "On" : "Turn on"}</span>
            </button>
          )}
          {lock.pinSet && (
            <button onClick={lock.lockNow} className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left text-go-body text-ink-primary active:bg-slate-50 first:rounded-t-xl last:rounded-b-xl">
              <Lock size={18} className="text-ink-tertiary" /><span className="flex-1">Lock now</span>
            </button>
          )}
        </Card>
        <p className={`px-1 mt-2 text-go-sub ${lock.pinRequired && !lock.pinSet ? "text-amber-800" : "text-ink-secondary"}`}>
          {lock.pinRequired && !lock.pinSet ? "You'll need a PIN to take payments or make pay links on this phone. " : ""}
          After 5 idle minutes Tekly asks for it before taking money, and it locks the app after 10.
          {lock.pinSet && faceOk ? " Only turn on Face ID / Touch ID if this phone is yours: anyone it recognises can use it." : ""}
        </p>
      </Section>

      <Section title="Notifications">
        <Card>
          <button onClick={togglePush} disabled={push === "busy" || push === "unsupported" || push === "not-configured" || push === "denied" || push === null}
            className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left text-go-body text-ink-primary active:bg-slate-50 rounded-xl disabled:opacity-100">
            {push === "on" ? <Bell size={18} className="text-primary" /> : <BellOff size={18} className="text-ink-tertiary" />}
            <span className="flex-1">Job alerts on this phone</span>
            <span className={`text-go-sub font-semibold ${push === "on" ? "text-primary" : "text-ink-tertiary"}`}>
              {push === "on" ? "On" : push === "busy" ? "…" : push === "off" ? "Turn on" : "Off"}
            </span>
          </button>
          {push && pushNote[push] && <p className="px-4 pb-3 -mt-1 text-go-sub text-ink-secondary">{pushNote[push]}</p>}
        </Card>
      </Section>

      <Section title="Appearance">
        <Card className="divide-y-[0.5px] divide-slate-200">
          <Choice value="system" current={theme} onPick={pickTheme}><Monitor size={18} className="text-ink-tertiary" />Same as phone</Choice>
          <Choice value="light" current={theme} onPick={pickTheme}><Sun size={18} className="text-ink-tertiary" />Light</Choice>
          <Choice value="dark" current={theme} onPick={pickTheme}><Moon size={18} className="text-ink-tertiary" />Dark</Choice>
        </Card>
      </Section>

      <Card className="divide-y-[0.5px] divide-slate-200">
        <a href="/" className="min-h-[3rem] flex items-center gap-3 px-4 text-ink-primary active:bg-slate-50 rounded-t-xl"><ExternalLink size={18} className="text-ink-tertiary" />Open the full Tekly app</a>
        <button onClick={signOut} className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left font-semibold text-rose-700 active:bg-slate-50 rounded-b-xl"><LogOut size={18} />Sign out</button>
      </Card>
    </Page>
  );
}
