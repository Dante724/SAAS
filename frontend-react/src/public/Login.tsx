import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ago } from "../components/DeviceList";
import { safeNext } from "../lib/safeNext";
import { ArrowLeft, Eye, EyeOff } from "lucide-react";
import Button from "../components/Button";
import { AuthShell, Field, FootLink, Notice, Submit, inputCls as input } from "./authUi";

// React sign-in page — replaces the legacy vanilla-JS auth shell.
// Contract: POST /api/auth/token (form) → { access_token }. With 2-step verification on,
// the server answers 401 + `X-MFA-Required: 1` until the form also carries the 6-digit code
// (or a recovery code) in `client_secret`, so we ask for it as a third step.
// Token is stored as `ros_tok` (same key the app + api client read), then we
// hand off to the redesign at "/".
// Design: email first, then password, then the code (the Apple / Tesla pattern) — one field
// and one solid button per step, the real app mark, nothing to sell. Always light, like every
// customer page (PublicLayout).

type Step = "email" | "password" | "code" | "limit";
type LimitDevice = { id: string; device: string; signed_in_at: string | null; last_active_at: string | null };

export default function Login() {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  // At the shop's device limit: which devices this login is using, and the short-lived token
  // that lets the person sign one out and finish signing in here.
  const [limit, setLimit] = useState<{ n: number; sessions: LimitDevice[]; token: string } | null>(null);
  const [pick, setPick] = useState("");
  const passRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (step === "password") passRef.current?.focus(); if (step === "code" && !busy) codeRef.current?.focus(); }, [step, busy]);

  // If already signed in, skip the form. 
  useEffect(() => {
    try {
      if (localStorage.getItem("ros_tok")) window.location.replace(safeNext(window.location.search));
    } catch { /* ignore */ }
  }, []);

  function finish(token: string) {
    localStorage.setItem("ros_tok", token);
    window.location.replace(safeNext(window.location.search));   // same-site ?next= only (Tekly Go)
  }

  async function continueAtLimit() {
    if (!limit || !pick) { setErr("Choose a device to sign out"); return; }
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/auth/session-limit/continue", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit_token: limit.token, session_id: pick }) });
      const j = await r.json().catch(() => ({}));
      if (r.status === 409 && j?.detail?.limit_token) {   // the limit was lowered meanwhile: pick another
        setLimit({ n: j.detail.limit, sessions: j.detail.sessions || [], token: j.detail.limit_token }); setPick("");
        setErr("You're still over the limit. Choose one more device to sign out."); setBusy(false); return;
      }
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Could not sign in");
      finish(j.access_token);
    } catch (e: any) { setErr(e.message || "Could not sign in"); setBusy(false); }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (step === "limit") return continueAtLimit();
    if (step === "email") {
      if (!/^\S+@\S+\.\S+$/.test(email.trim())) { setErr("Enter the email you use for your shop"); return; }
      setErr(null); setStep("password"); return;
    }
    if (!pass) { setErr("Enter your password"); return; }
    if (step === "code" && !code.trim()) { setErr("Enter the code from your authenticator app"); return; }
    setBusy(true); setErr(null);
    try {
      const fd = new FormData();
      fd.append("username", email.trim());
      fd.append("password", pass);
      if (step === "code") fd.append("client_secret", code.replace(/\s+/g, ""));
      const r = await fetch("/api/auth/token", { method: "POST", body: fd });
      const j = await r.json().catch(() => ({}));
      if (r.status === 401 && r.headers.get("X-MFA-Required") === "1") {
        const retry = step === "code";
        setStep("code"); setCode(""); setBusy(false);
        setErr(retry ? "That code didn't work. Try the newest code, or a recovery code." : null);
        return;
      }
      if (r.status === 409 && r.headers.get("X-Session-Limit") === "1" && j?.detail?.limit_token) {
        setLimit({ n: j.detail.limit, sessions: j.detail.sessions || [], token: j.detail.limit_token });
        setPick(""); setStep("limit"); setBusy(false);
        return;
      }
      if (r.status === 429) throw new Error("Too many attempts. Wait a few minutes and try again.");
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Could not sign in");
      finish(j.access_token);
    } catch (e: any) {
      setErr(e.message || "Could not sign in");
      setBusy(false);
    }
  }

  function startOver() { setStep("email"); setCode(""); setPass(""); setErr(null); setLimit(null); setPick(""); }
  const capsCheck = (e: KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.("CapsLock") ?? false);

  const title = step === "limit" ? "You're signed in on too many devices"
    : step === "code" ? "2-step verification" : "Sign in to Tekly";
  const sub = step === "limit" ? `Your shop allows ${limit?.n ?? ""} devices per person. Sign out of one to continue here.`
    : step === "code" ? "Enter the 6-digit code from your authenticator app, or a recovery code."
    : step === "password" ? "Enter your password to continue." : "Use the email you use for your shop.";
  const cta = step === "email" ? "Continue" : step === "code" ? "Verify" : step === "limit" ? "Sign out of it and continue" : "Sign in";

  return (
    <AuthShell title={title} sub={sub}>
        <form onSubmit={submit} noValidate>
          <div className="space-y-3">
            {step === "email" && (
              <Field id="login-email" label="Email">
                <input id="login-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
                  className={input} placeholder=" " autoFocus />
              </Field>
            )}

            {step !== "email" && (
              <div className="flex h-11 items-center gap-3 rounded-xl border border-slate-200 bg-white pl-2 pr-1">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-sm font-semibold uppercase text-ink-secondary">{email.trim()[0]}</span>
                <span className="min-w-0 flex-1 truncate text-base">{email.trim()}</span>
                <Button variant="link" onClick={startOver} className="px-2">
                  {step === "code" ? "Use a different account" : "Change"}
                </Button>
              </div>
            )}

            {step === "password" && (<>
              <Field id="login-password" label="Password"
                right={<button type="button" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"}
                  className="absolute right-2 top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-lg text-ink-tertiary hover:text-ink-primary">
                  {show ? <EyeOff size={19} /> : <Eye size={19} />}</button>}>
                <input id="login-password" ref={passRef} type={show ? "text" : "password"} autoComplete="current-password" value={pass}
                  onChange={(e) => setPass(e.target.value)} onKeyUp={capsCheck} onKeyDown={capsCheck}
                  className={`${input} pr-14`} placeholder=" " />
              </Field>
              {caps && <p className="px-1 text-sm text-amber-600">Caps Lock is on</p>}
            </>)}

            {step === "limit" && limit && (
              <fieldset className="space-y-2">
                <legend className="sr-only">Your signed-in devices</legend>
                {limit.sessions.map((d) => (
                  <label key={d.id} className={`flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-3 ${pick === d.id ? "border-primary" : "border-slate-200"}`}>
                    <input type="radio" name="limit-device" value={d.id} checked={pick === d.id} onChange={() => setPick(d.id)} className="accent-primary" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-base font-medium">{d.device}</span>
                      <span className="block text-sm text-ink-tertiary">{ago(d.last_active_at)}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
            )}

            {step === "code" && (
              <Field id="login-code" label="Code">
                <input id="login-code" ref={codeRef} inputMode="numeric" autoComplete="one-time-code" value={code}
                  onChange={(e) => setCode(e.target.value)} maxLength={32}
                  className={`${input} text-xl font-medium tracking-widest`} placeholder=" " />
              </Field>
            )}
          </div>

          <Notice err={err} />

          <Submit busy={busy} busyText={step === "code" ? "Checking…" : "Signing in…"}>{cta}</Submit>

          {step === "limit" && (<>
            <p className="mt-4 text-center text-sm text-ink-tertiary">Using a shared front-desk device? Ask your manager about it.</p>
            <div className="mt-3 flex justify-center"><Button variant="ghost" onClick={startOver} icon={<ArrowLeft size={15} />}>Cancel</Button></div>
          </>)}

          {step === "password" && (
            <p className="mt-5 text-center"><a href="/reset-password" className="text-sm font-medium text-primary hover:underline">Forgot password?</a></p>
          )}
          {step === "code" && (
            <div className="mt-5 flex justify-center"><Button variant="ghost" onClick={() => { setStep("password"); setErr(null); setCode(""); }} icon={<ArrowLeft size={15} />}>Back</Button></div>
          )}
          {step === "email" && (
            <FootLink>New to Tekly? <a href="/signup" className="hit44 font-medium text-primary hover:underline">Create your shop</a></FootLink>
          )}
        </form>
    </AuthShell>
  );
}
