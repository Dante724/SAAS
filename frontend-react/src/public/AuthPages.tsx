import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, CheckCircle2, ChevronDown, Eye, EyeOff, Loader2, MailCheck, XCircle } from "lucide-react";
import { AuthShell, Field, FootLink, Notice, Submit, inputCls, selectCls } from "./authUi";
import { signupTimezone } from "../lib/serverTime";

// React auth deep-links (replace the legacy vanilla-JS shell):
//   /signup            → create org + first user   POST /api/auth/register
//   /reset-password    → no token: ask for the email  POST /api/auth/password/forgot
//                        with ?token=: set a new one  POST /api/auth/password/reset
//   /verify-email      → confirm address           POST /api/auth/email/verify
//   /accept-invite     → join an org               POST /api/auth/invite/accept
// All share the sign-in page's look (authUi.tsx).

const qs = (k: string) => new URLSearchParams(location.search).get(k) || "";
const link = "font-medium text-primary hover:underline";

async function postJson(path: string, body: any) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Something went wrong");
  return j;
}

function Password({ id, label, value, onChange, autoFocus, autoComplete = "new-password" }: {
  id: string; label: string; value: string; onChange: (v: string) => void; autoFocus?: boolean; autoComplete?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <Field id={id} label={label} right={
      <button type="button" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"}
        className="absolute right-2 top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-lg text-ink-tertiary hover:text-ink-primary">
        {show ? <EyeOff size={19} /> : <Eye size={19} />}
      </button>}>
      <input id={id} type={show ? "text" : "password"} autoComplete={autoComplete} value={value} onChange={(e) => onChange(e.target.value)}
        className={`${inputCls} pr-14`} placeholder=" " autoFocus={autoFocus} />
    </Field>
  );
}

function Select({ id, label, value, onChange, options }: {
  id: string; label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[];
}) {
  return (
    <Field id={id} label={label} select right={<ChevronDown size={17} className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />}>
      <select id={id} className={selectCls} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Field>
  );
}

// ── Sign up: two short steps (your shop, then you) ─────────────────────────
type Country = { code: string; label: string; currency_code: string; timezone: string };
type Opts = { templates: { key: string; label: string }[]; regions: { key: string; label: string }[]; region_defaults: Record<string, { currency_code?: string }>; employee_brackets: string[]; countries?: Country[]; country_hint?: string | null };
const OTHER_COUNTRY = "other";

// The marketing site hosts the legal pages; bump TERMS_VERSION when they change.
const SITE_URL = "https://tekly.app";
const TERMS_VERSION = "2026-10-19";

export function Signup() {
  const [o, setO] = useState<Opts | null>(null);
  const [step, setStep] = useState<1 | 2>(1);
  const [f, setF] = useState({ org: "", name: "", email: "", pass: "", region: "", currency: "", template: "", employees: "", phone: "", country: "" });
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/signup-options").then((r) => r.json()).then((d: Opts) => {
      setO(d);
      // Pre-select the region from the CDN country hint when present (a suggestion,
      // not a lock — the user can change it); else the first option.
      const hinted = (d as any).region_hint && d.regions?.some((r) => r.key === (d as any).region_hint)
        ? (d as any).region_hint : (d.regions?.[0]?.key || "");
      // Launch B4: the Country picker (currency, tax and timezone follow it). Pre-selected from the
      // same CDN hint; a country we don't list yet falls back to "Other" and the region defaults.
      const countries = d.countries || [];
      const listed = countries.find((c) => c.code === d.country_hint);
      const pick = listed || (d.country_hint ? undefined : countries[0]);
      setF((s) => ({
        ...s,
        template: d.templates?.[0]?.key || "",
        region: hinted,
        country: countries.length ? (pick?.code || OTHER_COUNTRY) : "",
        employees: d.employee_brackets?.[0] || "",
        currency: pick?.currency_code || d.region_defaults?.[hinted]?.currency_code || "",
      }));
    }).catch(() => setO({ templates: [], regions: [], region_defaults: {}, employee_brackets: [] }));
  }, []);

  function set<K extends keyof typeof f>(k: K, v: string) {
    setF((s) => {
      const n = { ...s, [k]: v };
      if (k === "region" && o) n.currency = o.region_defaults?.[v]?.currency_code || n.currency;
      if (k === "country" && o) n.currency = o.countries?.find((c) => c.code === v)?.currency_code || n.currency;
      return n;
    });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (step === 1) {
      if (!f.org.trim()) { setErr("Enter your shop's name"); return; }
      setErr(null); setStep(2); return;
    }
    if (!f.name.trim() || !f.email.trim()) { setErr("Enter your name and email"); return; }
    if (!f.pass || f.pass.length < 8) { setErr("Password must be at least 8 characters"); return; }
    if (!agree) { setErr("Please accept the Terms of service and Privacy policy"); return; }
    setBusy(true); setErr(null);
    let tz: string | null = null;
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { /* ignore */ }
    const country = o?.countries?.find((c) => c.code === f.country);
    tz = signupTimezone(country?.timezone, tz);
    try {
      const d = await postJson("/api/auth/register", {
        org_name: f.org, name: f.name, email: f.email, password: f.pass,
        industry_template: f.template || null, region: country ? null : (f.region || null),
        country: country ? country.code : null, timezone: tz,
        currency_code: f.currency || null, currency: f.currency || null,
        employee_count: f.employees || null, company_phone: f.phone || null,
        accept_terms: true, terms_version: TERMS_VERSION,
      });
      localStorage.setItem("ros_tok", d.access_token);
      window.location.replace("/");
    } catch (e: any) { setErr(e.message || "Could not create the account"); setBusy(false); }
  }

  const opt = (xs: { key: string; label: string }[] = []) => xs.map((x) => ({ value: x.key, label: x.label }));
  return (
    <AuthShell title={step === 1 ? "Create your shop" : "Now, about you"} wide
      sub={<><span className="font-medium text-ink-primary">Step {step} of 2</span> · {step === 1 ? "Tell us about your shop." : "You'll use this to sign in."}</>}>
      <form onSubmit={submit} noValidate>
        {step === 1 ? (
          <div className="space-y-3">
            <Field id="su-org" label="Shop name">
              <input id="su-org" className={inputCls} value={f.org} onChange={(e) => set("org", e.target.value)} placeholder=" " autoFocus />
            </Field>
            <Select id="su-template" label="What you repair" value={f.template} onChange={(v) => set("template", v)} options={opt(o?.templates)} />
            <div className="grid grid-cols-2 gap-3">
              {o?.countries?.length ? (
                <Select id="su-country" label="Country" value={f.country} onChange={(v) => set("country", v)}
                  options={[...o.countries.map((c) => ({ value: c.code, label: c.label })), { value: OTHER_COUNTRY, label: "Other" }]} />
              ) : (
                <Select id="su-region" label="Region" value={f.region} onChange={(v) => set("region", v)} options={opt(o?.regions)} />
              )}
              <Field id="su-currency" label="Currency">
                <input id="su-currency" className={inputCls} value={f.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} placeholder=" " maxLength={3} />
              </Field>
            </div>
            <Select id="su-team" label="Team size" value={f.employees} onChange={(v) => set("employees", v)}
              options={(o?.employee_brackets || []).map((b) => ({ value: b, label: b }))} />
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field id="su-name" label="Your name">
                <input id="su-name" className={inputCls} value={f.name} onChange={(e) => set("name", e.target.value)} placeholder=" " autoComplete="name" autoFocus />
              </Field>
              <Field id="su-phone" label="Phone (optional)">
                <input id="su-phone" className={inputCls} value={f.phone} onChange={(e) => set("phone", e.target.value)} placeholder=" " autoComplete="tel" inputMode="tel" />
              </Field>
            </div>
            <Field id="su-email" label="Email">
              <input id="su-email" type="email" className={inputCls} value={f.email} onChange={(e) => set("email", e.target.value)} placeholder=" " autoComplete="username" />
            </Field>
            <Password id="su-pass" label="Password (8+ characters)" value={f.pass} onChange={(v) => set("pass", v)} />
            <label htmlFor="su-agree" className="flex items-start gap-2.5 pt-1 text-[14px] text-ink-secondary">
              <input id="su-agree" type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-0.5 h-4 w-4 shrink-0" />
              <span>I agree to the <a href={`${SITE_URL}/terms/`} target="_blank" rel="noopener" className={link}>Terms of service</a> and <a href={`${SITE_URL}/privacy/`} target="_blank" rel="noopener" className={link}>Privacy policy</a>.</span>
            </label>
          </div>
        )}
        <Notice err={err} />
        <Submit busy={busy} busyText="Creating your shop…">{step === 1 ? "Continue" : "Create shop"}</Submit>
        {step === 2 && (
          <button type="button" onClick={() => { setStep(1); setErr(null); }}
            className="mx-auto mt-5 flex items-center gap-1.5 text-[14px] font-medium text-ink-secondary hover:text-ink-primary">
            <ArrowLeft size={15} />Back
          </button>
        )}
        <FootLink>Already have an account? <a href="/login" className={link}>Sign in</a></FootLink>
      </form>
    </AuthShell>
  );
}

// ── Reset password ──────────────────────────────────────────────────────────
// Without a token this is the "forgot password" page (send a link); with one, set the new password.
export function ResetPassword() {
  const token = qs("token");
  return token ? <SetNewPassword token={token} /> : <ForgotPassword />;
}

function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) { setErr("Enter the email you use for your shop"); return; }
    setBusy(true); setErr(null);
    try { await postJson("/api/auth/password/forgot", { email: email.trim() }); setSent(true); }
    catch (e: any) { setErr(e.message || "Could not send the link. Try again in a few minutes."); }
    finally { setBusy(false); }
  }

  if (sent)
    return (
      <AuthShell title="Check your email" icon={<span className="grid h-14 w-14 place-items-center rounded-2xl bg-primary/10 text-primary"><MailCheck size={28} /></span>}
        sub={<>If <span className="font-medium text-ink-primary">{email.trim()}</span> has a Tekly account, a link to reset your password is on its way. It works for 1 hour.</>}>
        <a href="/login" className="flex h-[52px] w-full items-center justify-center rounded-xl bg-primary text-[16px] font-semibold text-white hover:bg-primary-container">Back to sign in</a>
        <p className="mt-5 text-center text-[14px] text-ink-secondary">No email? Check spam, or <button type="button" onClick={() => setSent(false)} className={link}>try another address</button>.</p>
      </AuthShell>
    );

  return (
    <AuthShell title="Reset your password" sub="Enter your email and we'll send you a link to set a new one.">
      <form onSubmit={submit} noValidate>
        <Field id="fp-email" label="Email">
          <input id="fp-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} placeholder=" " autoFocus />
        </Field>
        <Notice err={err} />
        <Submit busy={busy} busyText="Sending…">Send reset link</Submit>
        <FootLink>Remembered it? <a href="/login" className={link}>Back to sign in</a></FootLink>
      </form>
    </AuthShell>
  );
}

function SetNewPassword({ token }: { token: string }) {
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
      setOk("Password updated. Taking you to sign in…");
      setTimeout(() => window.location.replace("/login"), 1200);
    } catch (e: any) { setErr(e.message || "This reset link is invalid or has expired"); setBusy(false); }
  }
  return (
    <AuthShell title="Set a new password" sub="Use at least 8 characters. You'll be signed out on your other devices.">
      <form onSubmit={submit} noValidate>
        <div className="space-y-3">
          <Password id="rp-new" label="New password" value={p1} onChange={setP1} autoFocus />
          <Password id="rp-confirm" label="Confirm password" value={p2} onChange={setP2} />
        </div>
        <Notice err={err} ok={ok} />
        <Submit busy={busy} busyText="Saving…">Set new password</Submit>
        <FootLink><a href="/login" className={link}>Back to sign in</a></FootLink>
      </form>
    </AuthShell>
  );
}

// ── Verify email ────────────────────────────────────────────────────────────
export function VerifyEmail() {
  const token = qs("token");
  const [state, setState] = useState<"working" | "ok" | "fail">("working");
  useEffect(() => {
    postJson("/api/auth/email/verify", { token }).then(() => setState("ok")).catch(() => setState("fail"));
  }, []);
  const icon = state === "working" ? <span className="grid h-14 w-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Loader2 size={28} className="animate-spin" /></span>
    : state === "ok" ? <span className="grid h-14 w-14 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-600"><CheckCircle2 size={30} /></span>
    : <span className="grid h-14 w-14 place-items-center rounded-2xl bg-rose-500/10 text-rose-600"><XCircle size={30} /></span>;
  return (
    <AuthShell icon={icon}
      title={state === "ok" ? "Email verified" : state === "fail" ? "This link didn't work" : "Verifying your email"}
      sub={state === "ok" ? "Your address is confirmed. You can sign in now." : state === "fail" ? "It may have expired or already been used. Ask your shop's admin to send a new one." : "One moment…"}>
      {state !== "working" && (
        <a href="/login" className="flex h-[52px] w-full items-center justify-center rounded-xl bg-primary text-[16px] font-semibold text-white hover:bg-primary-container">Go to sign in</a>
      )}
    </AuthShell>
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
      <AuthShell title="This invite didn't work" icon={<span className="grid h-14 w-14 place-items-center rounded-2xl bg-rose-500/10 text-rose-600"><XCircle size={30} /></span>}
        sub="It may have expired or already been used. Ask your shop's admin to send a new one.">
        <a href="/login" className="flex h-[52px] w-full items-center justify-center rounded-xl bg-primary text-[16px] font-semibold text-white hover:bg-primary-container">Go to sign in</a>
      </AuthShell>
    );

  return (
    <AuthShell title={info?.org_name ? `Join ${info.org_name}` : "Join your team"}
      sub={info?.email ? <>You were invited as <span className="font-medium text-ink-primary">{info.email}</span>. Set a password to get started.</> : "Set a password to get started."}>
      <form onSubmit={submit} noValidate>
        <div className="space-y-3">
          <Field id="ai-name" label="Your name">
            <input id="ai-name" className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder=" " autoComplete="name" autoFocus />
          </Field>
          <Password id="ai-pass" label="Password (8+ characters)" value={pass} onChange={setPass} />
        </div>
        <Notice err={err} />
        <Submit busy={busy} busyText="Joining…">Join and sign in</Submit>
      </form>
    </AuthShell>
  );
}
