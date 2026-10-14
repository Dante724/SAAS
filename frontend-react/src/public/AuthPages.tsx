import { useEffect, useState, type ReactNode, type FormEvent } from "react";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";

// React auth deep-links (replace the legacy vanilla-JS shell):
//   /signup            → create org + first user   POST /api/auth/register
//   /reset-password    → set new password          POST /api/auth/password/reset
//   /verify-email      → confirm address           POST /api/auth/email/verify
//   /accept-invite     → join an org               POST /api/auth/invite/accept
// All share the same flat, on-brand shell as <Login/>.

const qs = (k: string) => new URLSearchParams(location.search).get(k) || "";
const GRAD = "linear-gradient(135deg,#2c72d9,#6848be)";

function useDeviceTheme() {
  useEffect(() => {
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const set = () => document.documentElement.classList.toggle("dark", mq.matches);
      set(); mq.addEventListener("change", set);
    } catch { /* ignore */ }
  }, []);
}

function Shell({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  useDeviceTheme();
  return (
    <div className="min-h-screen bg-surface font-sans text-ink-primary">
      <div className="mx-auto grid min-h-screen max-w-md place-items-center px-5">
        <div className="w-full">
          <div className="mb-6 flex items-center gap-3">
            <div className="grid h-12 w-12 place-items-center rounded-2xl text-lg font-extrabold text-white" style={{ background: GRAD }}>R</div>
            <div>
              <div className="text-xl font-extrabold tracking-[-.3px]">RepairOS</div>
              <div className="text-xs font-semibold text-ink-tertiary">{sub || "Workshop management"}</div>
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-6 sm:p-7">
            <h1 className="text-lg font-extrabold text-ink-primary">{title}</h1>
            {children}
          </div>
          <div className="mt-6 text-center text-[11.5px] text-ink-tertiary">RepairOS · secure</div>
        </div>
      </div>
    </div>
  );
}

const inputCls =
  "mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary";
const labelCls = "mt-4 block text-[13px] font-bold text-ink-secondary";
const btnCls =
  "mt-5 flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-bold text-white disabled:opacity-60";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (<><label className={labelCls}>{label}</label>{children}</>);
}
function Err({ msg }: { msg: string | null }) {
  return msg ? <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] font-semibold text-rose-600">{msg}</div> : null;
}
function Ok({ msg }: { msg: string | null }) {
  return msg ? <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-[13px] font-semibold text-emerald-700">{msg}</div> : null;
}

async function postJson(path: string, body: any) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Something went wrong");
  return j;
}

// ── Sign up ────────────────────────────────────────────────────────────────
type Opts = { templates: { key: string; label: string }[]; regions: { key: string; label: string }[]; region_defaults: Record<string, { currency_code?: string }>; employee_brackets: string[] };
export function Signup() {
  const [o, setO] = useState<Opts | null>(null);
  const [f, setF] = useState({ org: "", name: "", email: "", pass: "", region: "", currency: "", template: "", employees: "", phone: "", country: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/signup-options").then((r) => r.json()).then((d: Opts) => {
      setO(d);
      // Pre-select the region from the CDN country hint when present (a suggestion,
      // not a lock — the user can change it); else the first option.
      const hinted = (d as any).region_hint && d.regions?.some((r) => r.key === (d as any).region_hint)
        ? (d as any).region_hint : (d.regions?.[0]?.key || "");
      setF((s) => ({
        ...s,
        template: d.templates?.[0]?.key || "",
        region: hinted,
        employees: d.employee_brackets?.[0] || "",
        currency: d.region_defaults?.[hinted]?.currency_code || "",
      }));
    }).catch(() => setO({ templates: [], regions: [], region_defaults: {}, employee_brackets: [] }));
  }, []);

  function set<K extends keyof typeof f>(k: K, v: string) {
    setF((s) => {
      const n = { ...s, [k]: v };
      if (k === "region" && o) n.currency = o.region_defaults?.[v]?.currency_code || n.currency;
      return n;
    });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!f.org || !f.name || !f.email) { setErr("Company, name and email are required"); return; }
    if (!f.pass || f.pass.length < 8) { setErr("Password must be at least 8 characters"); return; }
    setBusy(true); setErr(null);
    let tz: string | null = null;
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { /* ignore */ }
    try {
      const d = await postJson("/api/auth/register", {
        org_name: f.org, name: f.name, email: f.email, password: f.pass,
        industry_template: f.template || null, region: f.region || null,
        country: f.country || null, timezone: tz,
        currency_code: f.currency || null, currency: f.currency || null,
        employee_count: f.employees || null, company_phone: f.phone || null,
      });
      localStorage.setItem("ros_tok", d.access_token);
      window.location.replace("/");
    } catch (e: any) { setErr(e.message || "Could not create the account"); setBusy(false); }
  }

  return (
    <Shell title="Create your shop" sub="Get started in a minute">
      <p className="mt-1 text-[13px] text-ink-tertiary">Set up your workspace and sign in.</p>
      <form onSubmit={submit}>
        <div className="grid grid-cols-2 gap-x-3">
          <div className="col-span-2"><Field label="Company name"><input className={inputCls} value={f.org} onChange={(e) => set("org", e.target.value)} placeholder="Acme Repairs" autoFocus /></Field></div>
          <div><Field label="Your name"><input className={inputCls} value={f.name} onChange={(e) => set("name", e.target.value)} /></Field></div>
          <div><Field label="Phone"><input className={inputCls} value={f.phone} onChange={(e) => set("phone", e.target.value)} /></Field></div>
          <div className="col-span-2"><Field label="Email"><input type="email" autoComplete="username" className={inputCls} value={f.email} onChange={(e) => set("email", e.target.value)} placeholder="you@shop.com" /></Field></div>
          <div className="col-span-2"><Field label="Password"><input type="password" autoComplete="new-password" className={inputCls} value={f.pass} onChange={(e) => set("pass", e.target.value)} placeholder="At least 8 characters" /></Field></div>
          <div><Field label="Industry"><select className={inputCls} value={f.template} onChange={(e) => set("template", e.target.value)}>{o?.templates?.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</select></Field></div>
          <div><Field label="Region"><select className={inputCls} value={f.region} onChange={(e) => set("region", e.target.value)}>{o?.regions?.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select></Field></div>
          <div><Field label="Currency"><input className={inputCls} value={f.currency} onChange={(e) => set("currency", e.target.value)} placeholder="USD" /></Field></div>
          <div><Field label="Team size"><select className={inputCls} value={f.employees} onChange={(e) => set("employees", e.target.value)}>{o?.employee_brackets?.map((b) => <option key={b} value={b}>{b}</option>)}</select></Field></div>
        </div>
        <Err msg={err} />
        <button type="submit" disabled={busy} className={btnCls} style={{ background: GRAD }}>
          {busy && <Loader2 size={16} className="animate-spin" />}{busy ? "Creating…" : "Create shop & sign in"}
        </button>
        <div className="mt-4 text-center text-[13px]"><span className="text-ink-tertiary">Already have an account? </span><a href="/login" className="font-semibold text-primary hover:underline">Sign in</a></div>
      </form>
    </Shell>
  );
}

// ── Reset password ──────────────────────────────────────────────────────────
export function ResetPassword() {
  const token = qs("token");
  const [p1, setP1] = useState(""); const [p2, setP2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!p1 || p1.length < 8) { setErr("Password must be at least 8 characters"); return; }
    if (p1 !== p2) { setErr("Passwords do not match"); return; }
    setBusy(true); setErr(null);
    try {
      await postJson("/api/auth/password/reset", { token, new_password: p1 });
      setOk("Password updated — redirecting to sign in…");
      setTimeout(() => window.location.replace("/login"), 1200);
    } catch (e: any) { setErr(e.message || "This reset link is invalid or has expired"); setBusy(false); }
  }
  return (
    <Shell title="Set a new password" sub="Password reset">
      <form onSubmit={submit}>
        <Field label="New password"><input type="password" className={inputCls} value={p1} onChange={(e) => setP1(e.target.value)} autoFocus /></Field>
        <Field label="Confirm password"><input type="password" className={inputCls} value={p2} onChange={(e) => setP2(e.target.value)} /></Field>
        <Err msg={err} /><Ok msg={ok} />
        <button type="submit" disabled={busy} className={btnCls} style={{ background: GRAD }}>{busy && <Loader2 size={16} className="animate-spin" />}{busy ? "Saving…" : "Set new password"}</button>
        <div className="mt-4 text-center text-[13px]"><a href="/login" className="font-semibold text-primary hover:underline">Back to sign in</a></div>
      </form>
    </Shell>
  );
}

// ── Verify email ────────────────────────────────────────────────────────────
export function VerifyEmail() {
  const token = qs("token");
  const [state, setState] = useState<"working" | "ok" | "fail">("working");
  useEffect(() => {
    postJson("/api/auth/email/verify", { token }).then(() => setState("ok")).catch(() => setState("fail"));
  }, []);
  return (
    <Shell title={state === "ok" ? "Email verified" : state === "fail" ? "Link invalid or expired" : "Verifying…"} sub="Email verification">
      <div className="mt-4 flex flex-col items-center gap-3 py-2 text-center">
        {state === "working" && <Loader2 size={30} className="animate-spin text-primary" />}
        {state === "ok" && <CheckCircle2 size={34} className="text-emerald-600" />}
        {state === "fail" && <XCircle size={34} className="text-rose-500" />}
        <div className="text-[13px] text-ink-tertiary">
          {state === "ok" && "Your address is confirmed. You can sign in now."}
          {state === "fail" && "Ask an administrator to resend your verification email."}
          {state === "working" && "Confirming your email address…"}
        </div>
        {state !== "working" && <a href="/login" className="mt-1 rounded-lg px-4 py-2 text-sm font-bold text-white" style={{ background: GRAD }}>Go to sign in</a>}
      </div>
    </Shell>
  );
}

// ── Accept invite ───────────────────────────────────────────────────────────
export function AcceptInvite() {
  const token = qs("token");
  const [info, setInfo] = useState<{ valid: boolean; org_name?: string; email?: string; name?: string } | null>(null);
  const [name, setName] = useState(""); const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    postJson("/api/auth/invite/preview", { token })
      .then((r) => { setInfo(r); if (r.name) setName(r.name); })
      .catch(() => setInfo({ valid: false }));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setErr("Enter your name"); return; }
    if (!pass || pass.length < 8) { setErr("Password must be at least 8 characters"); return; }
    setBusy(true); setErr(null);
    try {
      const d = await postJson("/api/auth/invite/accept", { token, name, password: pass });
      localStorage.setItem("ros_tok", d.access_token);
      window.location.replace("/");
    } catch (e: any) { setErr(e.message || "This invitation is invalid or has expired"); setBusy(false); }
  }

  if (info && !info.valid)
    return (
      <Shell title="Invitation invalid or expired" sub="Team invite">
        <p className="mt-2 text-[13px] text-ink-tertiary">Ask your administrator to send a new invite.</p>
        <a href="/login" className="mt-4 inline-block font-semibold text-primary hover:underline">Back to sign in</a>
      </Shell>
    );

  return (
    <Shell title={info?.org_name ? `Join ${info.org_name}` : "Accept your invite"} sub="Team invite">
      <p className="mt-1 text-[13px] text-ink-tertiary">{info?.email ? `Invited as ${info.email}. ` : ""}Set a password to continue.</p>
      <form onSubmit={submit}>
        <Field label="Your name"><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} autoFocus /></Field>
        <Field label="Password"><input type="password" autoComplete="new-password" className={inputCls} value={pass} onChange={(e) => setPass(e.target.value)} placeholder="At least 8 characters" /></Field>
        <Err msg={err} />
        <button type="submit" disabled={busy} className={btnCls} style={{ background: GRAD }}>{busy && <Loader2 size={16} className="animate-spin" />}{busy ? "Joining…" : "Accept & sign in"}</button>
      </form>
    </Shell>
  );
}
