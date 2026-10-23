import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { safeNext } from "../lib/safeNext";
import { ArrowLeft, Eye, EyeOff } from "lucide-react";
import { AuthShell, Field, FootLink, Notice, Submit, inputCls as input } from "./authUi";

// React sign-in page — replaces the legacy vanilla-JS auth shell.
// Contract: POST /api/auth/token (form) → { access_token }. With 2-step verification on,
// the server answers 401 + `X-MFA-Required: 1` until the form also carries the 6-digit code
// (or a recovery code) in `client_secret`, so we ask for it as a third step.
// Token is stored as `ros_tok` (same key the app + api client read), then we
// hand off to the redesign at "/".
// Design: email first, then password, then the code (the Apple / Tesla pattern) — one field
// and one solid button per step, the real app mark, nothing to sell. Every colour is a theme
// token, so the page is wholly light or wholly dark with the device.

type Step = "email" | "password" | "code";

export default function Login() {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const passRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (step === "password") passRef.current?.focus(); if (step === "code" && !busy) codeRef.current?.focus(); }, [step, busy]);

  // If already signed in, skip the form. (AuthShell follows the device theme.)
  useEffect(() => {
    try {
      if (localStorage.getItem("ros_tok")) window.location.replace(safeNext(window.location.search));
    } catch { /* ignore */ }
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
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
      if (r.status === 429) throw new Error("Too many attempts. Wait a few minutes and try again.");
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Could not sign in");
      localStorage.setItem("ros_tok", j.access_token);
      window.location.replace(safeNext(window.location.search));
    } catch (e: any) {
      setErr(e.message || "Could not sign in");
      setBusy(false);
    }
  }

  function startOver() { setStep("email"); setCode(""); setPass(""); setErr(null); }
  const capsCheck = (e: KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.("CapsLock") ?? false);

  const title = step === "code" ? "2-step verification" : "Sign in to Tekly";
  const sub = step === "code" ? "Enter the 6-digit code from your authenticator app, or a recovery code."
    : step === "password" ? "Enter your password to continue." : "Use the email you use for your shop.";
  const cta = step === "email" ? "Continue" : step === "code" ? "Verify" : "Sign in";

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
              <div className="flex h-[52px] items-center gap-3 rounded-xl border border-slate-200 px-3">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-[13px] font-semibold uppercase text-ink-secondary">{email.trim()[0]}</span>
                <span className="min-w-0 flex-1 truncate text-[15px]">{email.trim()}</span>
                <button type="button" onClick={startOver} className="text-[14px] font-medium text-primary hover:underline">
                  {step === "code" ? "Use a different account" : "Change"}
                </button>
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
              {caps && <p className="px-1 text-[13px] text-amber-600">Caps Lock is on</p>}
            </>)}

            {step === "code" && (
              <Field id="login-code" label="Code">
                <input id="login-code" ref={codeRef} inputMode="numeric" autoComplete="one-time-code" value={code}
                  onChange={(e) => setCode(e.target.value)} maxLength={32}
                  className={`${input} text-[20px] font-medium tracking-[.3em]`} placeholder=" " />
              </Field>
            )}
          </div>

          <Notice err={err} />

          <Submit busy={busy} busyText={step === "code" ? "Checking…" : "Signing in…"}>{cta}</Submit>

          {step === "password" && (
            <p className="mt-5 text-center"><a href="/reset-password" className="text-[14px] font-medium text-primary hover:underline">Forgot password?</a></p>
          )}
          {step === "code" && (
            <button type="button" onClick={() => { setStep("password"); setErr(null); setCode(""); }}
              className="mx-auto mt-5 flex items-center gap-1.5 text-[14px] font-medium text-ink-secondary hover:text-ink-primary">
              <ArrowLeft size={15} />Back
            </button>
          )}
          {step === "email" && (
            <FootLink>New to Tekly? <a href="/signup" className="font-medium text-primary hover:underline">Create your shop</a></FootLink>
          )}
        </form>
    </AuthShell>
  );
}
