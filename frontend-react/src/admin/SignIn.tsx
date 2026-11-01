import { useState, type FormEvent } from "react";
import { adminApi, AdminApiError } from "./api";

type Step =
  | { kind: "password" }
  | { kind: "code"; ticket: string }
  | { kind: "setup"; ticket: string; secret?: string; uri?: string }
  | { kind: "recovery"; codes: string[] };

const input = "w-full px-3 py-2 rounded-lg border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary";
const primaryBtn = "w-full py-2.5 rounded-lg bg-primary text-white text-sm font-semibold hover:opacity-90 disabled:opacity-60";

/** Password, then a 2-step code. First sign-in sets up the authenticator app and shows
 *  the recovery codes once. */
export default function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setErr(""); setBusy(true);
    try { await fn(); } catch (e) { setErr(e instanceof AdminApiError ? e.message : "Couldn't reach Tekly. Try again."); }
    finally { setBusy(false); }
  }

  const submitPassword = (e: FormEvent) => { e.preventDefault(); run(async () => {
    const r = await adminApi.post<{ next: string; ticket: string }>("/login", { email, password });
    setPassword(""); setCode("");
    if (r.next === "mfa_setup") {
      const s = await adminApi.post<{ secret: string; uri: string }>("/mfa/setup", { ticket: r.ticket });
      setStep({ kind: "setup", ticket: r.ticket, secret: s.secret, uri: s.uri });
    } else setStep({ kind: "code", ticket: r.ticket });
  }); };

  const submitCode = (e: FormEvent) => { e.preventDefault(); if (step.kind !== "code") return; run(async () => {
    await adminApi.post("/login/mfa", useRecovery ? { ticket: step.ticket, recovery_code: code.trim() } : { ticket: step.ticket, code: code.trim() });
    onSignedIn();
  }); };

  const confirmSetup = (e: FormEvent) => { e.preventDefault(); if (step.kind !== "setup") return; run(async () => {
    const r = await adminApi.post<{ recovery_codes: string[] }>("/mfa/setup/confirm", { ticket: step.ticket, code: code.trim() });
    setStep({ kind: "recovery", codes: r.recovery_codes });
  }); };

  return (
    <div className="min-h-screen bg-slate-50 grid place-items-center px-4">
      <div className="w-full max-w-sm bg-white rounded-2xl border border-slate-200 shadow-sm p-7">
        <div className="mb-6">
          <div className="text-xs font-semibold text-primary">Tekly admin</div>
          <h1 className="text-xl font-semibold text-ink-primary mt-1">
            {step.kind === "password" ? "Sign in" : step.kind === "setup" ? "Set up 2-step sign-in" : step.kind === "recovery" ? "Save your recovery codes" : "Enter your code"}
          </h1>
        </div>

        {step.kind === "password" && (
          <form onSubmit={submitPassword} className="space-y-3">
            <label className="block text-xs font-semibold text-ink-secondary">Email
              <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} className={`${input} mt-1`} /></label>
            <label className="block text-xs font-semibold text-ink-secondary">Password
              <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} className={`${input} mt-1`} /></label>
            <button disabled={busy} className={primaryBtn}>{busy ? "Checking…" : "Continue"}</button>
          </form>
        )}

        {step.kind === "code" && (
          <form onSubmit={submitCode} className="space-y-3">
            <p className="text-sm text-ink-secondary">{useRecovery ? "Enter one of your recovery codes. Each works once." : "Open your authenticator app and enter the 6-digit code for Tekly admin."}</p>
            <input autoFocus inputMode={useRecovery ? "text" : "numeric"} autoComplete="one-time-code" required value={code}
              onChange={(e) => setCode(e.target.value)} className={`${input} tracking-widest text-center text-lg`} aria-label={useRecovery ? "Recovery code" : "Code"} />
            <button disabled={busy} className={primaryBtn}>{busy ? "Checking…" : "Sign in"}</button>
            <button type="button" onClick={() => { setUseRecovery((v) => !v); setCode(""); }} className="w-full text-xs text-primary font-semibold">
              {useRecovery ? "Use a code from my app" : "Lost your phone? Use a recovery code"}</button>
          </form>
        )}

        {step.kind === "setup" && (
          <form onSubmit={confirmSetup} className="space-y-3">
            <p className="text-sm text-ink-secondary">Admin accounts need a second step. In Google Authenticator, 1Password or a similar app, add an account with this key:</p>
            <div className="font-mono text-sm bg-slate-50 border border-slate-200 rounded-lg p-3 break-all select-all" data-testid="mfa-secret">{step.secret}</div>
            <a href={step.uri} className="block text-xs text-primary font-semibold">Open in my authenticator app</a>
            <label className="block text-xs font-semibold text-ink-secondary">Then enter the 6-digit code it shows
              <input inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} className={`${input} mt-1 tracking-widest text-center text-lg`} /></label>
            <button disabled={busy} className={primaryBtn}>{busy ? "Checking…" : "Turn on 2-step sign-in"}</button>
          </form>
        )}

        {step.kind === "recovery" && (
          <div className="space-y-3">
            <p className="text-sm text-ink-secondary">If you lose your phone, each of these codes signs you in once. Store them somewhere safe. They won't be shown again.</p>
            <div className="grid grid-cols-2 gap-2 font-mono text-sm bg-slate-50 border border-slate-200 rounded-lg p-3 select-all">
              {step.codes.map((c) => <span key={c}>{c}</span>)}
            </div>
            <button onClick={onSignedIn} className={primaryBtn}>I've saved them</button>
          </div>
        )}

        {err && <p role="alert" className="mt-3 text-sm text-rose-600">{err}</p>}
      </div>
    </div>
  );
}
