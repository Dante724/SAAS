import { useState, type FormEvent } from "react";
import { adminApi, AdminApiError } from "./api";
import { TeklyMark } from "../components/brand/TeklyLogo";
import Button from "../components/Button";
import { Field, Input } from "../components/Field";

type Step =
  | { kind: "password" }
  | { kind: "code"; ticket: string }
  | { kind: "setup"; ticket: string; secret?: string; uri?: string }
  | { kind: "recovery"; codes: string[] };


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
    <div className="min-h-screen bg-canvas grid place-items-center px-4 font-sans">
      <div className="w-full max-w-sm bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-7">
        <div className="mb-6">
          <div className="flex items-center gap-2"><TeklyMark size={24} /><span className="text-sm font-semibold text-ink-primary">Tekly admin</span></div>
          <h1 className="text-xl font-semibold text-ink-primary mt-4">
            {step.kind === "password" ? "Sign in" : step.kind === "setup" ? "Set up 2-step sign-in" : step.kind === "recovery" ? "Save your recovery codes" : "Enter your code"}
          </h1>
        </div>

        {step.kind === "password" && (
          <form onSubmit={submitPassword} className="space-y-3">
            <Field label="Email" htmlFor="adm-email"><Input id="adm-email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
            <Field label="Password" htmlFor="adm-pass"><Input id="adm-pass" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
            <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">{busy ? "Checking…" : "Continue"}</Button>
          </form>
        )}

        {step.kind === "code" && (
          <form onSubmit={submitCode} className="space-y-3">
            <p className="text-sm text-ink-secondary">{useRecovery ? "Enter one of your recovery codes. Each works once." : "Open your authenticator app and enter the 6-digit code for Tekly admin."}</p>
            <Input autoFocus inputMode={useRecovery ? "text" : "numeric"} autoComplete="one-time-code" required value={code}
              onChange={(e) => setCode(e.target.value)} className="h-11 tracking-widest text-center text-lg" aria-label={useRecovery ? "Recovery code" : "Code"} />
            <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">{busy ? "Checking…" : "Sign in"}</Button>
            <div className="flex justify-center"><Button variant="link" onClick={() => { setUseRecovery((v) => !v); setCode(""); }}>
              {useRecovery ? "Use a code from my app" : "Lost your phone? Use a recovery code"}</Button></div>
          </form>
        )}

        {step.kind === "setup" && (
          <form onSubmit={confirmSetup} className="space-y-3">
            <p className="text-sm text-ink-secondary">Admin accounts need a second step. In Google Authenticator, 1Password or a similar app, add an account with this key:</p>
            <div className="font-mono text-sm bg-slate-50 border border-slate-200 rounded-lg p-3 break-all select-all" data-testid="mfa-secret">{step.secret}</div>
            <a href={step.uri} className="block text-xs text-primary font-semibold hover:underline">Open in my authenticator app</a>
            <Field label="Then enter the 6-digit code it shows" htmlFor="adm-setup-code">
              <Input id="adm-setup-code" inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} className="h-11 tracking-widest text-center text-lg" /></Field>
            <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">{busy ? "Checking…" : "Turn on 2-step sign-in"}</Button>
          </form>
        )}

        {step.kind === "recovery" && (
          <div className="space-y-3">
            <p className="text-sm text-ink-secondary">If you lose your phone, each of these codes signs you in once. Store them somewhere safe. They won't be shown again.</p>
            <div className="grid grid-cols-2 gap-2 font-mono text-sm bg-slate-50 border border-slate-200 rounded-lg p-3 select-all">
              {step.codes.map((c) => <span key={c}>{c}</span>)}
            </div>
            <Button variant="primary" size="lg" onClick={onSignedIn} className="w-full">I've saved them</Button>
          </div>
        )}

        {err && <p role="alert" className="mt-3 text-sm text-rose-600">{err}</p>}
      </div>
    </div>
  );
}
