// Quick re-check before money, and the auto-lock (rules in lib/idle.ts).
// - confirmMoney(): resolves true straight away when the phone is fresh; otherwise asks for the
//   PIN (or Face ID) in a sheet. Someone who may take payments but has no PIN yet sets one here
//   with their password (on by default for anyone with can_take_payments / pay links).
// - The lock screen covers everything after 10 idle minutes, shows who is signed in, and offers
//   Sign out. It only appears for people who have a PIN (nothing to unlock with otherwise).
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Delete, Lock as LockIcon, LogOut, ScanFace } from "lucide-react";
import { useMe } from "../../context/Me";
import { activity, initIdle, needsRecheck, readStored, sessionKey, tick, verified, writeStored, type IdleState } from "../lib/idle";
import { passkeyCheck, passkeyOn } from "../lib/passkey";
import { setPin, verifyPin } from "../lib/pin";
import { haptic } from "../lib/scanner";
import { signOut } from "../session";
import { PrimaryButton, Sheet } from "./ui";

type Ask = { mode: "verify" | "setup"; what: string; resolve: (ok: boolean) => void };
type Ctx = {
  confirmMoney: (what: string) => Promise<boolean>;
  askPin: (what: string) => Promise<boolean>;   // always ask (turning Face ID on, say)
  setupPin: () => Promise<boolean>;
  lockNow: () => void;
  pinSet: boolean;
  pinRequired: boolean;
};
const LockCtx = createContext<Ctx>({
  confirmMoney: async () => true, askPin: async () => true, setupPin: async () => true, lockNow: () => {}, pinSet: false, pinRequired: false,
});
export const useLock = () => useContext(LockCtx);

const token = () => { try { return localStorage.getItem("ros_tok"); } catch { return null; } };
const initials = (name?: string) => (name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

export function LockProvider({ children }: { children: ReactNode }) {
  const { me, can, refresh } = useMe();
  const session = useRef(sessionKey(token()));
  const st = useRef<IdleState>(initIdle(Date.now(), readStored(session.current)));
  const [locked, setLocked] = useState(st.current.locked);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [pinSetLocal, setPinSetLocal] = useState<boolean | null>(null);
  const pinSet = pinSetLocal ?? !!me?.pin_set;
  const pinRequired = !!me && (can("can_take_payments") || can("can_create_payment_links"));
  const lastWrite = useRef(0);

  const apply = useCallback((next: IdleState) => {
    st.current = next;
    setLocked(next.locked);
    const now = Date.now();
    if (now - lastWrite.current > 5000 || next.locked) { lastWrite.current = now; writeStored(session.current, next.lastActive); }
  }, []);

  useEffect(() => {
    const onAct = () => { if (document.visibilityState === "visible") apply(activity(st.current, Date.now())); };
    const onTick = () => { if (document.visibilityState === "visible") apply(tick(st.current, Date.now())); };
    const onHide = () => { if (document.visibilityState === "hidden") writeStored(session.current, st.current.lastActive); };
    window.addEventListener("pointerdown", onAct, { capture: true, passive: true });
    window.addEventListener("keydown", onAct, { capture: true });
    document.addEventListener("visibilitychange", onTick);
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pageshow", onTick);
    window.addEventListener("focus", onTick);
    const t = setInterval(onTick, 15_000);
    return () => {
      window.removeEventListener("pointerdown", onAct, { capture: true });
      window.removeEventListener("keydown", onAct, { capture: true });
      document.removeEventListener("visibilitychange", onTick);
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pageshow", onTick);
      window.removeEventListener("focus", onTick);
      clearInterval(t);
    };
  }, [apply]);

  const done = useCallback(() => apply(verified(st.current, Date.now())), [apply]);

  const open = useCallback((mode: Ask["mode"], what: string) => new Promise<boolean>((resolve) => {
    setAsk((prev) => { prev?.resolve(false); return { mode, what, resolve }; });
  }), []);

  const confirmMoney = useCallback(async (what: string) => {
    if (!pinSet) return pinRequired ? open("setup", what) : true;
    if (!needsRecheck(st.current, Date.now())) return true;
    return open("verify", what);
  }, [pinSet, pinRequired, open]);

  const askPin = useCallback((what: string) => (pinSet ? open("verify", what) : open("setup", what)), [pinSet, open]);
  const setupPin = useCallback(() => open("setup", pinSet ? "Change your PIN" : "Set up your PIN"), [pinSet, open]);
  const lockNow = useCallback(() => { if (pinSet) apply({ ...st.current, locked: true, stale: true }); }, [pinSet, apply]);

  const finish = (ok: boolean) => {
    if (!ask) return;
    if (ok) done();
    ask.resolve(ok);
    setAsk(null);
  };

  return (
    <LockCtx.Provider value={{ confirmMoney, askPin, setupPin, lockNow, pinSet, pinRequired }}>
      {children}
      <Sheet open={!!ask && !(locked && pinSet)} onClose={() => finish(false)} title={ask?.mode === "setup" ? (pinSet ? "Change your PIN" : "Set a payment PIN") : "Enter your PIN"}>
        {ask?.mode === "verify" && (
          <div className="px-2 pb-2">
            <p className="text-center text-[0.9375rem] text-ink-secondary">{ask.what}. The phone was idle, so check it's you.</p>
            <PinEntry uid={me?.id} onSubmit={verifyPin} onOk={() => finish(true)} />
          </div>
        )}
        {ask?.mode === "setup" && (
          <SetupForm pinSet={pinSet} what={ask.what} onDone={async () => { setPinSetLocal(true); refresh().catch(() => {}); finish(true); }} />
        )}
      </Sheet>
      {locked && pinSet && <LockScreen name={me?.name} uid={me?.id} onUnlock={() => { if (ask?.mode === "verify") finish(true); else done(); }} />}
    </LockCtx.Provider>
  );
}

/** Dots + keypad. Works with a hardware keyboard on iPad too. */
export function PinEntry({ uid, onSubmit, onOk }: { uid?: string; onSubmit: (pin: string) => Promise<string | null>; onOk: () => void }) {
  const [pin, setPinVal] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const face = passkeyOn(uid);

  const submit = useCallback(async (p: string) => {
    if (p.length < 4 || busy) return;
    setBusy(true);
    const why = await onSubmit(p);
    setBusy(false);
    if (why) { setErr(why); setPinVal(""); navigator.vibrate?.([30, 40, 30]); return; }
    haptic();
    onOk();
  }, [busy, onSubmit, onOk]);

  const press = useCallback((k: string) => {
    setErr(null);
    setPinVal((p) => (k === "del" ? p.slice(0, -1) : p.length < 6 ? p + k : p));
  }, []);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (/^\d$/.test(e.key)) { press(e.key); e.preventDefault(); }
      else if (e.key === "Backspace") { press("del"); e.preventDefault(); }
      else if (e.key === "Enter") { submit(pin); e.preventDefault(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [press, submit, pin]);

  async function tryFace() {
    if (await passkeyCheck(uid)) { haptic(); onOk(); }
    else setErr("Face ID didn't work. Use your PIN.");
  }

  const key = "h-[4.25rem] w-[4.25rem] mx-auto rounded-full text-[1.625rem] font-medium tabular-nums text-ink-primary bg-white border border-slate-200 shadow-clay-sm active:bg-slate-100 inline-flex items-center justify-center disabled:opacity-40";
  return (
    <div className="mx-auto w-full max-w-[18rem]">
      <div className="mt-4 flex justify-center gap-3" aria-label={`${pin.length} digits entered`} role="status">
        {Array.from({ length: Math.max(4, pin.length) }).map((_, i) => (
          <span key={i} className={`h-3.5 w-3.5 rounded-full border-2 ${i < pin.length ? "bg-ink-primary border-ink-primary" : "border-slate-300"}`} />
        ))}
      </div>
      <p className={`mt-3 min-h-[1.25rem] text-center text-[0.875rem] ${err ? "text-rose-700" : "text-ink-tertiary"}`} aria-live="polite">{err || "4 to 6 digits"}</p>
      <div className="mt-3 grid grid-cols-3 gap-y-3">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => <button key={d} type="button" onClick={() => press(d)} disabled={busy} className={key}>{d}</button>)}
        {face ? <button type="button" onClick={tryFace} aria-label="Use Face ID or Touch ID" className={`${key} !bg-transparent !border-transparent !shadow-none text-primary`}><ScanFace size={28} /></button> : <span />}
        <button type="button" onClick={() => press("0")} disabled={busy} className={key}>0</button>
        <button type="button" onClick={() => press("del")} aria-label="Delete" disabled={busy || !pin} className={`${key} !bg-transparent !border-transparent !shadow-none text-ink-secondary`}><Delete size={24} /></button>
      </div>
      <PrimaryButton className="mt-5" onClick={() => submit(pin)} disabled={pin.length < 4 || busy}>{busy ? "Checking…" : "Continue"}</PrimaryButton>
    </div>
  );
}

function SetupForm({ pinSet, what, onDone }: { pinSet: boolean; what: string; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [pin, setPinVal] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save() {
    if (busy) return;
    if (pin !== again) { setErr("The two PINs don't match"); return; }
    setBusy(true);
    const why = await setPin(pin, password);
    setBusy(false);
    if (why) { setErr(why); return; }
    haptic();
    onDone();
  }
  const field = "mt-1 w-full min-h-[3rem] rounded-xl border border-slate-200 bg-white px-3 text-[1.0625rem] text-ink-primary focus:outline-none focus:border-primary";
  return (
    <form className="px-2 pb-2 space-y-3" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <p className="text-[0.9375rem] text-ink-secondary">
        {pinSet ? "Pick a new PIN." : `${what}: set a PIN first.`} Tekly asks for it before taking money once the phone has been idle 5 minutes, and to unlock after 10.
      </p>
      <label className="block text-[0.8125rem] font-semibold text-ink-tertiary">Your Tekly password
        <input type="password" autoComplete="current-password" value={password} onChange={(e) => { setPassword(e.target.value); setErr(null); }} className={field} />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-[0.8125rem] font-semibold text-ink-tertiary">New PIN
          <input type="password" inputMode="numeric" autoComplete="off" maxLength={6} value={pin} onChange={(e) => { setPinVal(e.target.value.replace(/\D/g, "")); setErr(null); }} className={`${field} tracking-[0.3em]`} />
        </label>
        <label className="block text-[0.8125rem] font-semibold text-ink-tertiary">Again
          <input type="password" inputMode="numeric" autoComplete="off" maxLength={6} value={again} onChange={(e) => { setAgain(e.target.value.replace(/\D/g, "")); setErr(null); }} className={`${field} tracking-[0.3em]`} />
        </label>
      </div>
      {err && <p className="text-[0.875rem] text-rose-700" role="alert">{err}</p>}
      <PrimaryButton type="submit" disabled={!password || pin.length < 4 || busy}>{busy ? "Saving…" : "Save PIN"}</PrimaryButton>
    </form>
  );
}

function LockScreen({ name, uid, onUnlock }: { name?: string; uid?: string; onUnlock: () => void }) {
  return (
    <div className="fixed inset-0 z-[80] bg-canvas text-ink-primary overflow-y-auto pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]"
      role="dialog" aria-modal="true" aria-label="Tekly Go is locked">
      <div className="min-h-full flex flex-col items-center justify-center px-4 py-8">
        <div className="w-full max-w-sm md:bg-white md:rounded-[1.75rem] md:border md:border-slate-100 md:shadow-clay-card md:px-6 md:py-8 text-center">
          <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-[1.25rem] bg-primary text-white text-[1.375rem] font-bold">{initials(name)}</span>
          <p className="mt-3 inline-flex items-center gap-1.5 text-[0.8125rem] font-semibold text-ink-tertiary"><LockIcon size={14} />Locked</p>
          <h1 className="mt-0.5 text-[1.5rem] font-bold tracking-tight">{name || "Signed in"}</h1>
          <p className="text-[0.9375rem] text-ink-secondary">Enter your PIN to carry on</p>
          <PinEntry uid={uid} onSubmit={verifyPin} onOk={onUnlock} />
          <button type="button" onClick={signOut} className="mt-4 min-h-[2.75rem] px-4 inline-flex items-center gap-2 rounded-2xl text-[0.9375rem] font-semibold text-rose-700 active:bg-slate-100">
            <LogOut size={18} />Not {(name || "you").split(" ")[0]}? Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
