import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowLeft, Eye, EyeOff, KeyRound, Loader2, Lock, Mail, PackageCheck, ShieldCheck, Smartphone, Wrench } from "lucide-react";

// React sign-in page — replaces the legacy vanilla-JS auth shell.
// Contract: POST /api/auth/token (form) → { access_token }. With 2-step verification on,
// the server answers 401 + `X-MFA-Required: 1` until the form also carries the 6-digit code
// (or a recovery code) in `client_secret`, so we ask for it as a second step.
// Token is stored as `ros_tok` (same key the app + api client read), then we
// hand off to the redesign at "/".
// Theme: every colour on the form side is a theme token (bg-canvas / bg-white / ink / slate), so
// the page is wholly light or wholly dark with the device. The brand panel is deliberately dark
// in both themes.

const BRAND = "linear-gradient(135deg,#2c72d9,#6848be)";
const FEATURES = [
  { icon: Wrench, title: "Every job in one place", text: "Work orders, estimates and invoices, from intake to pickup." },
  { icon: PackageCheck, title: "Parts that update the job", text: "When a purchase order arrives, the part goes straight to the job." },
  { icon: Smartphone, title: "Customers stay in the loop", text: "They approve extras and track repairs from their phone." },
];

export default function Login() {
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
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
  const capsCheck = (e: KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.("CapsLock") ?? false);

  const field = "w-full rounded-xl border border-slate-300 bg-white py-3 pl-11 pr-3 text-[15px] text-ink-primary outline-none transition placeholder:text-ink-tertiary focus:border-primary focus:ring-4 focus:ring-primary/15";

  return (
    <div className="min-h-screen bg-canvas font-sans text-ink-primary lg:grid lg:grid-cols-[1.05fr_1fr]">
      {/* Brand panel (desktop) — dark in both themes on purpose */}
      <aside className="relative hidden overflow-hidden lg:flex lg:flex-col lg:justify-between p-12 text-white"
        style={{ background: "radial-gradient(900px 600px at 15% 10%, rgba(44,114,217,.55), transparent 60%), radial-gradient(800px 600px at 90% 90%, rgba(104,72,190,.55), transparent 60%), #0b1020" }}>
        <div className="pointer-events-none absolute inset-0 opacity-[.07]"
          style={{ backgroundImage: "linear-gradient(rgba(255,255,255,.6) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.6) 1px, transparent 1px)", backgroundSize: "44px 44px" }} />
        <div className="relative flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl text-lg font-bold shadow-lg" style={{ background: BRAND }}>R</div>
          <span className="text-xl font-bold tracking-[-.3px]">RepairOS</span>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-[44px] font-bold leading-[1.05] tracking-[-1.5px]">Run the whole repair.<br /><span className="text-white/60">From one place.</span></h2>
          <ul className="mt-10 space-y-6">
            {FEATURES.map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-4">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[rgba(255,255,255,.1)] ring-1 ring-[rgba(255,255,255,.15)]"><Icon size={19} /></span>
                <span><span className="block text-[15px] font-semibold">{title}</span><span className="block text-sm text-white/65">{text}</span></span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-[13px] text-white/50">For phone, computer, tools, small engine, auto, marine and fleet repair shops.</p>
      </aside>

      {/* Form side — follows the device theme */}
      <main className="grid min-h-screen place-items-center px-5 py-10">
        <div className="w-full max-w-[420px]">
          {/* brand header (phones and tablets) */}
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="grid h-11 w-11 place-items-center rounded-2xl text-lg font-bold text-white" style={{ background: BRAND }}>R</div>
            <div>
              <div className="text-xl font-bold tracking-[-.3px] text-ink-primary">RepairOS</div>
              <div className="text-[13px] text-ink-tertiary">Run the whole repair, from one place.</div>
            </div>
          </div>

          <form onSubmit={submit} className="rounded-3xl border border-slate-200 bg-white p-7 shadow-clay-card sm:p-8">
            {needCode ? (<>
              <span className="grid h-12 w-12 place-items-center rounded-2xl bg-primary/10 text-primary"><ShieldCheck size={22} /></span>
              <h1 className="mt-4 text-[22px] font-bold tracking-[-.4px] text-ink-primary">2-step verification</h1>
              <p className="mt-1.5 text-sm text-ink-secondary">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>

              <label htmlFor="login-code" className="mt-6 block text-[13px] font-semibold text-ink-secondary">Code</label>
              <div className="relative mt-1.5">
                <KeyRound size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />
                <input id="login-code" ref={codeRef} inputMode="numeric" autoComplete="one-time-code" value={code}
                  onChange={(e) => setCode(e.target.value)} maxLength={32}
                  className={`${field} text-center text-xl font-semibold tracking-[.35em]`} placeholder="123456" />
              </div>
            </>) : (<>
              <h1 className="text-[26px] font-bold tracking-[-.6px] text-ink-primary">Welcome back</h1>
              <p className="mt-1.5 text-sm text-ink-secondary">Sign in to your shop.</p>

              <label htmlFor="login-email" className="mt-7 block text-[13px] font-semibold text-ink-secondary">Email</label>
              <div className="relative mt-1.5">
                <Mail size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />
                <input id="login-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
                  className={field} placeholder="you@shop.com" autoFocus />
              </div>

              <div className="mt-4 flex items-baseline justify-between">
                <label htmlFor="login-password" className="block text-[13px] font-semibold text-ink-secondary">Password</label>
                <a href="/reset-password" className="text-[13px] font-semibold text-primary hover:underline">Forgot password?</a>
              </div>
              <div className="relative mt-1.5">
                <Lock size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />
                <input id="login-password" type={show ? "text" : "password"} autoComplete="current-password" value={pass}
                  onChange={(e) => setPass(e.target.value)} onKeyUp={capsCheck} onKeyDown={capsCheck}
                  className={`${field} pr-12`} placeholder="Your password" />
                <button type="button" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"}
                  className="absolute right-2 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-ink-tertiary hover:bg-slate-100 hover:text-ink-primary">
                  {show ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
              {caps && <p className="mt-1.5 text-[12px] font-semibold text-amber-600">Caps Lock is on</p>}
            </>)}

            {err && <div role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[13px] font-semibold text-rose-700">{err}</div>}

            <button type="submit" disabled={busy}
              className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl py-3 text-[15px] font-semibold text-white shadow-[0_10px_24px_-10px_rgba(60,90,220,.7)] transition hover:brightness-110 active:scale-[.99] disabled:opacity-60"
              style={{ background: BRAND }}>
              {busy && <Loader2 size={17} className="animate-spin" />}{busy ? (needCode ? "Checking…" : "Signing in…") : (needCode ? "Verify" : "Sign in")}
            </button>

            {needCode ? (
              <button type="button" onClick={startOver} className="mt-5 flex items-center gap-1.5 text-[13px] font-semibold text-ink-secondary hover:text-ink-primary">
                <ArrowLeft size={15} />Use a different account
              </button>
            ) : (
              <p className="mt-6 text-center text-[13px] text-ink-secondary">New to RepairOS? <a href="/signup" className="font-semibold text-primary hover:underline">Create your shop</a></p>
            )}
          </form>

          <p className="mt-6 flex items-center justify-center gap-1.5 text-[12px] text-ink-tertiary"><Lock size={12} />Secure sign-in · 2-step verification supported</p>
        </div>
      </main>
    </div>
  );
}
