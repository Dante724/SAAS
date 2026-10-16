import { useEffect, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

// React sign-in page — replaces the legacy vanilla-JS auth shell.
// Contract (unchanged): POST /api/auth/token (form) → { access_token }.
// Token is stored as `ros_tok` (same key the app + api client read), then we
// hand off to the redesign at "/".

export default function Login() {
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
    setBusy(true); setErr(null);
    try {
      const fd = new FormData();
      fd.append("username", email.trim());
      fd.append("password", pass);
      const r = await fetch("/api/auth/token", { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Login failed");
      localStorage.setItem("ros_tok", j.access_token);
      window.location.replace("/");
    } catch (e: any) {
      setErr(e.message || "Could not sign in");
      setBusy(false);
    }
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
            <h1 className="text-lg font-bold text-ink-primary">Welcome back</h1>
            <p className="mt-1 text-[13px] text-ink-tertiary">Enter your details to continue.</p>

            <label className="mt-5 block text-[13px] font-bold text-ink-secondary">Email</label>
            <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary"
              placeholder="you@shop.com" autoFocus />

            <label className="mt-4 block text-[13px] font-bold text-ink-secondary">Password</label>
            <input type="password" autoComplete="current-password" value={pass} onChange={(e) => setPass(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary"
              placeholder="••••••••" />

            {err && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] font-semibold text-rose-600">{err}</div>}

            <button type="submit" disabled={busy}
              className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-bold text-white disabled:opacity-60"
              style={{ background: "linear-gradient(135deg,#2c72d9,#6848be)" }}>
              {busy && <Loader2 size={16} className="animate-spin" />}{busy ? "Signing in…" : "Sign in"}
            </button>

            <div className="mt-4 flex items-center justify-between text-[13px]">
              <a href="/reset-password" className="font-semibold text-primary hover:underline">Forgot password?</a>
              <a href="/signup" className="font-semibold text-ink-secondary hover:text-ink-primary">Create account</a>
            </div>
          </form>

          <div className="mt-6 text-center text-[11.5px] text-ink-tertiary">RepairOS · secure sign-in</div>
        </div>
      </div>
    </div>
  );
}
