import { useEffect, useRef, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

// React sign-in page — replaces the legacy vanilla-JS auth shell.
// Contract: POST /api/auth/token (form) → { access_token }. With 2-step verification on,
// the server answers 401 + `X-MFA-Required: 1` until the form also carries the 6-digit code
// (or a recovery code) in `client_secret`, so we ask for it as a second step.
// Token is stored as `ros_tok` (same key the app + api client read), then we
// hand off to the redesign at "/".

export default function Login() {
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [needCode, setNeedCode] = useState(false);
  const [code, setCode] = useState("");
  const codeRef = useRef<HTMLInputElement>(null);
  // Put the cursor in the code box whenever step two opens (or a code is refused).
  useEffect(() => { if (needCode && !busy) codeRef.current?.focus(); }, [needCode, busy]);

  // Follow the visitor's device theme, like the rest of the app's two themes.
  useEffect(() => {
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const set = () => document.documentElement.classList.toggle("dark", mq.matches);
      set(); mq.addEventListener("change", set);
    } catch { /* ignore */ }
    // if already signed in, skip the form
    try {
      if (localStorage.getItem("ros_tok")) window.location.replace("/");
    } catch { /* ignore */ }
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim() || !pass) { setErr("Enter your email and password"); return; }
    if (needCode && !code.trim()) { setErr("Enter the code from your authenticator app"); return; }
    setBusy(true); setErr(null);
    try {
      const fd = new FormData();
      fd.append("username", email.trim());
      fd.append("password", pass);
      if (needCode) fd.append("client_secret", code.replace(/\s+/g, ""));
      const r = await fetch("/api/auth/token", { method: "POST", body: fd });
      const j = await r.json().catch(() => ({}));
      if (r.status === 401 && r.headers.get("X-MFA-Required") === "1") {
        const retry = needCode;
        setNeedCode(true); setCode(""); setBusy(false);
        setErr(retry ? "That code didn't work. Try the newest code, or a recovery code." : null);
        return;
      }
      if (r.status === 429) throw new Error("Too many attempts. Wait a few minutes and try again.");
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Could not sign in");
      localStorage.setItem("ros_tok", j.access_token);
      window.location.replace("/");
    } catch (e: any) {
      setErr(e.message || "Could not sign in");
      setBusy(false);
    }
  }

  function startOver() {
    setNeedCode(false); setCode(""); setPass(""); setErr(null);
  }

  return (
    <div className="min-h-screen bg-surface font-sans text-ink-primary">
      <div className="mx-auto grid min-h-screen max-w-md place-items-center px-5">
        <div className="w-full">
          {/* brand */}
          <div className="mb-6 flex items-center gap-3">
            <div className="grid h-12 w-12 place-items-center rounded-2xl text-lg font-bold text-white"
              style={{ background: "linear-gradient(135deg,#2c72d9,#6848be)" }}>R</div>
            <div>
              <div className="text-xl font-bold tracking-[-.3px]">RepairOS</div>
              <div className="text-xs font-semibold text-ink-tertiary">Sign in to your workspace</div>
            </div>
          </div>

          <form onSubmit={submit} className="rounded-2xl border border-slate-200 bg-white p-6 sm:p-7">
            {needCode ? (<>
            <h1 className="text-lg font-bold text-ink-primary">2-step verification</h1>
            <p className="mt-1 text-[13px] text-ink-tertiary">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>

            <label htmlFor="login-code" className="mt-5 block text-[13px] font-bold text-ink-secondary">Code</label>
            <input id="login-code" ref={codeRef} inputMode="numeric" autoComplete="one-time-code" value={code}
              onChange={(e) => setCode(e.target.value)} maxLength={32}
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-center text-lg font-semibold tracking-[.3em] text-ink-primary outline-none focus:border-primary"
              placeholder="123456" />
            </>) : (<>
            <h1 className="text-lg font-bold text-ink-primary">Welcome back</h1>
            <p className="mt-1 text-[13px] text-ink-tertiary">Enter your details to continue.</p>

            <label htmlFor="login-email" className="mt-5 block text-[13px] font-bold text-ink-secondary">Email</label>
            <input id="login-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary"
              placeholder="you@shop.com" autoFocus />

            <label htmlFor="login-password" className="mt-4 block text-[13px] font-bold text-ink-secondary">Password</label>
            <input id="login-password" type="password" autoComplete="current-password" value={pass} onChange={(e) => setPass(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary"
              placeholder="••••••••" />
            </>)}

            {err && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] font-semibold text-rose-600">{err}</div>}

            <button type="submit" disabled={busy}
              className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-bold text-white disabled:opacity-60"
              style={{ background: "linear-gradient(135deg,#2c72d9,#6848be)" }}>
              {busy && <Loader2 size={16} className="animate-spin" />}{busy ? (needCode ? "Checking…" : "Signing in…") : (needCode ? "Verify" : "Sign in")}
            </button>

            {needCode ? (
            <div className="mt-4 text-[13px]">
              <button type="button" onClick={startOver} className="font-semibold text-ink-secondary hover:text-ink-primary">Use a different account</button>
            </div>
            ) : (
            <div className="mt-4 flex items-center justify-between text-[13px]">
              <a href="/reset-password" className="font-semibold text-primary hover:underline">Forgot password?</a>
              <a href="/signup" className="font-semibold text-ink-secondary hover:text-ink-primary">Create account</a>
            </div>
            )}
          </form>

          <div className="mt-6 text-center text-[11.5px] text-ink-tertiary">RepairOS · secure sign-in</div>
        </div>
      </div>
    </div>
  );
}
