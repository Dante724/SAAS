import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Check, ChevronLeft, MapPin, Phone, Clock, CalendarPlus, Loader2,
  Wrench, MessageSquare, User, Sparkles,
} from "lucide-react";

// ── Appointment 2.0 — public online appointment as a real React page ───────────────
// White-label wizard on the app's tokens: [location] → service (with a
// "Something else" catch-all) → [specialist] → time → details (phone-first
// recognition) → branded confirmation with .ics + directions. When nothing
// fits, the visitor leaves a request (lands as a web Lead) — never a dead end.

type Svc = { id: string; name: string; price?: number; duration_min?: number; category?: string | null; employee_ids?: string[] };
type Staff = { id: string; name: string };
type Loc = { id: string; name: string; address?: string | null; phone?: string | null; working_hours?: Record<string, { open?: string; close?: string; closed?: boolean }> };
type Cfg = {
  shop_name: string; logo_data?: string | null; color?: string | null;
  show_employee_step?: boolean; time_slot?: number; window_days?: number;
  services: Svc[]; staff: Staff[]; locations: Loc[];
};
type Day = { date: string; open: boolean; free: number };
type StepK = "loc" | "svc" | "emp" | "time" | "you";

const slug = location.pathname.split("/book/")[1]?.replace(/\/$/, "") || "";
const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function money(n?: number) {
  if (n == null || n === 0) return "Free";
  return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
function todayHours(l?: Loc | null) {
  const h = l?.working_hours; if (!h) return null;
  const d = h[WEEK[(new Date().getDay() + 6) % 7]];
  if (!d || d.closed) return "Closed today";
  return d.open && d.close ? `Open today ${d.open}–${d.close}` : null;
}
function fmtDay(iso: string) {
  const d = new Date(iso + "T00:00:00");
  return { dow: d.toLocaleDateString("en", { weekday: "short" }), num: d.getDate() };
}
function slotGroup(t: string) { const h = +t.split(":")[0]; return h < 12 ? "Morning" : h < 17 ? "Afternoon" : "Evening"; }
function icsFor(shop: string, svc: string, date: string, time: string, durMin: number, address?: string | null) {
  const pad = (n: number) => String(n).padStart(2, "0");
  const start = (date + "T" + time).replace(/[-:]/g, "") + "00";
  const end = new Date(new Date(`${date}T${time}`).getTime() + durMin * 60000);
  const endS = `${end.getFullYear()}${pad(end.getMonth() + 1)}${pad(end.getDate())}T${pad(end.getHours())}${pad(end.getMinutes())}00`;
  return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//RepairOS//appointment//EN", "BEGIN:VEVENT",
    `DTSTART:${start}`, `DTEND:${endS}`, `SUMMARY:${svc} — ${shop}`,
    address ? `LOCATION:${address.replace(/,/g, "\\,")}` : "", "END:VEVENT", "END:VCALENDAR"].filter(Boolean).join("\r\n");
}

export default function PublicAppointment() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // selections
  const [locId, setLocId] = useState<string | null>(null);
  const [svc, setSvc] = useState<Svc | null>(null);
  const [catchAll, setCatchAll] = useState(false);
  const [issue, setIssue] = useState("");
  const [empId, setEmpId] = useState<string | null>(null);
  const [days, setDays] = useState<Day[] | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [slots, setSlots] = useState<string[] | null>(null);
  const [time, setTime] = useState<string | null>(null);
  const [cat, setCat] = useState<string | null>(null);
  // details
  const [phone, setPhone] = useState(""); const [name, setName] = useState("");
  const [email, setEmail] = useState(""); const [note, setNote] = useState("");
  const [hello, setHello] = useState<string | null>(null);
  const helloAsked = useRef("");
  // flow — navigate by KEY: the employee step comes and goes per service,
  // so numeric indexes would shift under us.
  const [stepK, setStepK] = useState<StepK>("svc");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [done, setDone] = useState<{ kind: "booked"; when: string } | { kind: "requested" } | null>(null);
  const [reqMode, setReqMode] = useState(false);

  useEffect(() => {
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const set = () => document.documentElement.classList.toggle("dark", mq.matches);
      set(); mq.addEventListener("change", set);
    } catch { /* ignore */ }
    fetch("/api/public/appointment/" + slug).then(async (r) => {
      if (!r.ok) throw new Error();
      const c: Cfg = await r.json();
      setCfg(c);
      setLocId(c.locations?.[0]?.id || null);
      setStepK((c.locations || []).length > 1 ? "loc" : "svc");
    }).catch(() => setErr("This appointment page is unavailable."));
  }, []);

  const accent = cfg?.color || "#0059ba";
  const locs = cfg?.locations || [];
  const loc = locs.find((l) => l.id === locId) || locs[0] || null;
  const hasLocStep = locs.length > 1;

  const staffForSvc = (s: Svc | null) => {
    if (!cfg) return [];
    const allow = s?.employee_ids || [];
    return allow.length ? cfg.staff.filter((e) => allow.includes(e.id)) : cfg.staff;
  };
  const staffFor = staffForSvc(svc);
  const empApplicable = !!cfg?.show_employee_step && staffFor.length > 0 && !catchAll;

  const steps = useMemo<StepK[]>(() => {
    const s: StepK[] = [];
    if (hasLocStep) s.push("loc");
    s.push("svc");
    if (empApplicable) s.push("emp");
    s.push("time", "you");
    return s;
  }, [hasLocStep, empApplicable]);
  const at = Math.max(0, steps.indexOf(stepK));

  const cats = useMemo(() => {
    const set = new Set<string>();
    (cfg?.services || []).forEach((s) => s.category && set.add(s.category));
    return [...set];
  }, [cfg]);

  function back() {
    setFlash(null);
    if (stepK === "you") setStepK("time");
    else if (stepK === "time") setStepK(empApplicable ? "emp" : "svc");
    else if (stepK === "emp") setStepK("svc");
    else if (stepK === "svc" && hasLocStep) setStepK("loc");
  }

  async function loadDays(sv: Svc | null, emp: string | null) {
    setDays(null); setDate(null); setSlots(null); setTime(null);
    const q = new URLSearchParams({ days: "14" });
    if (locId) q.set("location_id", locId);
    if (sv) q.set("service_id", sv.id);
    if (emp) q.set("employee_id", emp);
    try {
      const r = await fetch(`/api/public/appointment/${slug}/days?` + q);
      const d = await r.json();
      const list: Day[] = d.days || [];
      setDays(list);
      const first = list.find((x) => x.open && x.free > 0);
      if (first) pickDay(first.date, sv, emp);
    } catch { setDays([]); }
  }
  async function pickDay(iso: string, sv: Svc | null = svc, emp: string | null = empId) {
    setDate(iso); setSlots(null); setTime(null);
    const q = new URLSearchParams({ date: iso });
    if (locId) q.set("location_id", locId);
    if (sv) q.set("service_id", sv.id);
    if (emp) q.set("employee_id", emp);
    try {
      const r = await fetch(`/api/public/appointment/${slug}/slots?` + q);
      setSlots((await r.json()).slots || []);
    } catch { setSlots([]); }
  }
  function goTime(sv: Svc | null, emp: string | null) { setStepK("time"); loadDays(sv, emp); }

  function chooseService(s: Svc) {
    setSvc(s); setCatchAll(false); setEmpId(null); setFlash(null);
    const empStep = !!cfg?.show_employee_step && staffForSvc(s).length > 0;
    if (empStep) setStepK("emp"); else goTime(s, null);
  }

  async function checkHello() {
    const digits = phone.replace(/\D/g, "");
    if (digits.length < 7 || helloAsked.current === digits) return;
    helloAsked.current = digits;
    try {
      const r = await fetch(`/api/public/appointment/${slug}/hello`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone }),
      });
      const d = await r.json();
      if (d.known) { setHello(d.name || "there"); if (d.name && !name) setName(d.name); }
      else setHello(null);
    } catch { /* silent */ }
  }

  async function book() {
    if (!name.trim()) { setFlash("Please enter your name"); return; }
    if (!phone.trim() && !email.trim()) { setFlash("A phone or email is needed to confirm"); return; }
    if (!date || !time) { setFlash("Pick a date and time first"); return; }
    setBusy(true); setFlash(null);
    try {
      const fullNote = [catchAll && issue.trim() ? `Issue: ${issue.trim()}` : null, note.trim() || null].filter(Boolean).join(" | ");
      const r = await fetch(`/api/public/appointment/${slug}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(), phone: phone.trim() || null, email: email.trim() || null,
          service_id: svc?.id || null, employee_id: empId, location_id: locId,
          date, time, note: fullNote || null,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Could not book");
      setDone({ kind: "booked", when: d.when });
    } catch (e: any) {
      setFlash(e.message || "Could not book — please try another slot");
      if (/taken|another|soon|closed|window/i.test(e.message || "")) { setStepK("time"); loadDays(svc, empId); }
    } finally { setBusy(false); }
  }

  async function sendRequest() {
    if (!name.trim()) { setFlash("Please enter your name"); return; }
    if (!phone.trim() && !email.trim()) { setFlash("A phone or email is needed so we can reach you"); return; }
    setBusy(true); setFlash(null);
    try {
      const r = await fetch(`/api/public/appointment/${slug}/request`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(), phone: phone.trim() || null, email: email.trim() || null,
          issue: (issue || note).trim() || null, service_name: svc?.name || null,
        }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "Could not send your request");
      setDone({ kind: "requested" });
    } catch (e: any) { setFlash(e.message || "Could not send your request"); }
    finally { setBusy(false); }
  }

  function downloadIcs() {
    if (!date || !time || !cfg) return;
    const ics = icsFor(cfg.shop_name, svc?.name || "Repair appointment", date, time, svc?.duration_min || cfg.time_slot || 60, loc?.address);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([ics], { type: "text/calendar" }));
    a.download = "appointment.ics"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  if (err) return <Shell accent="#0059ba"><div className="py-14 text-center text-ink-secondary">{err}</div></Shell>;
  if (!cfg) return <div className="min-h-screen bg-surface" />;

  const noAvailability = days !== null && !days.some((d) => d.open && d.free > 0);
  const grouped: Record<string, string[]> = {};
  (slots || []).forEach((t) => { (grouped[slotGroup(t)] ||= []).push(t); });

  const input = "mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary";
  const lbl = "mt-4 block text-[13px] font-bold text-ink-secondary";
  const cta = "mt-5 flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-bold text-white disabled:opacity-60";
  const cardBtn = "w-full rounded-xl border bg-white p-4 text-left transition hover:border-slate-400";

  // ── confirmation ──
  if (done) {
    return (
      <Shell accent={accent} cfg={cfg}>
        <div className="py-4 text-center">
          <div className="mx-auto grid h-14 w-14 place-items-center rounded-full" style={{ background: `${accent}1f` }}>
            <Check size={28} style={{ color: accent }} />
          </div>
          <h1 className="mt-4 text-xl font-extrabold text-ink-primary">
            {done.kind === "booked" ? "You're booked!" : "Request received"}
          </h1>
          <p className="mt-1.5 text-sm text-ink-secondary">
            {done.kind === "booked"
              ? <>{svc?.name || "Repair appointment"} · <b>{done.when}</b></>
              : <>Thanks {name.split(" ")[0] || ""} — {cfg.shop_name} will call you shortly to arrange a time.</>}
          </p>
          {done.kind === "booked" && (
            <button onClick={downloadIcs} className="mx-auto mt-4 flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-bold text-white" style={{ background: accent }}>
              <CalendarPlus size={16} /> Add to calendar
            </button>
          )}
          {(loc?.address || loc?.phone) && (
            <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4 text-left">
              <div className="flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-[.07em] text-ink-tertiary"><MapPin size={13} /> {loc?.name || "Find us"}</div>
              {loc?.address && <div className="mt-1.5 text-[13px] text-ink-secondary">{loc.address}</div>}
              {todayHours(loc) && <div className="mt-1 flex items-center gap-1.5 text-xs text-ink-tertiary"><Clock size={12} /> {todayHours(loc)}</div>}
              <div className="mt-3 flex gap-2">
                {loc?.address && <a target="_blank" rel="noopener" href={"https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(loc.address)} className="flex-1 rounded-lg border border-slate-300 py-2 text-center text-sm font-bold text-ink-secondary hover:bg-slate-50">Directions</a>}
                {loc?.phone && <a href={`tel:${loc.phone}`} className="flex flex-1 items-center justify-center gap-1.5 rounded-lg py-2 text-sm font-bold text-white" style={{ background: accent }}><Phone size={14} /> Call</a>}
              </div>
            </div>
          )}
        </div>
      </Shell>
    );
  }

  // ── no-dead-end request form ──
  if (reqMode) {
    return (
      <Shell accent={accent} cfg={cfg} onBack={() => { setReqMode(false); setFlash(null); }}>
        <h2 className="text-lg font-extrabold text-ink-primary">Leave a request</h2>
        <p className="mt-1 text-[13px] text-ink-tertiary">Tell us what you need and {cfg.shop_name} will call you back.</p>
        <label className={lbl}>What's the problem?</label>
        <textarea value={issue} onChange={(e) => setIssue(e.target.value)} rows={3} className={input + " resize-y"} placeholder="e.g. Screen cracked on an iPhone 14, flickers when touched…" />
        <label className={lbl}>Your name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} className={input} />
        <label className={lbl}>Phone</label>
        <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" className={input} />
        {flash && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] font-semibold text-rose-600">{flash}</div>}
        <button onClick={sendRequest} disabled={busy} className={cta} style={{ background: accent }}>
          {busy && <Loader2 size={16} className="animate-spin" />}{busy ? "Sending…" : "Send request"}
        </button>
      </Shell>
    );
  }

  return (
    <Shell accent={accent} cfg={cfg} steps={steps.length} at={at}
      onBack={at > 0 ? back : undefined}>
      {/* ── location ── */}
      {stepK === "loc" && (
        <>
          <h2 className="text-lg font-extrabold text-ink-primary">Where would you like to go?</h2>
          <div className="mt-4 flex flex-col gap-2.5">
            {locs.map((l) => (
              <button key={l.id} onClick={() => { setLocId(l.id); setStepK("svc"); }}
                className={cardBtn + " border-slate-200"}>
                <div className="font-bold text-ink-primary">{l.name}</div>
                {l.address && <div className="mt-0.5 text-[13px] text-ink-tertiary">{l.address}</div>}
                {todayHours(l) && <div className="mt-1 flex items-center gap-1.5 text-xs text-ink-tertiary"><Clock size={12} /> {todayHours(l)}</div>}
              </button>
            ))}
          </div>
        </>
      )}

      {/* ── service + catch-all ── */}
      {stepK === "svc" && (
        <>
          <h2 className="text-lg font-extrabold text-ink-primary">What can we help with?</h2>
          {cats.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {[null, ...cats].map((c) => (
                <button key={c ?? "all"} onClick={() => setCat(c)}
                  className="rounded-full border px-3.5 py-1.5 text-[13px] font-bold"
                  style={cat === c ? { background: accent, borderColor: accent, color: "#fff" } : { borderColor: "#c2c6d5", color: "#435577" }}>
                  {c ?? "All"}
                </button>
              ))}
            </div>
          )}
          <div className="mt-4 flex flex-col gap-2.5">
            {(cfg.services || []).filter((s) => !cat || s.category === cat).map((s) => (
              <button key={s.id} onClick={() => chooseService(s)}
                className={cardBtn + " border-slate-200 flex items-center justify-between gap-3"}>
                <span>
                  <span className="flex items-center gap-2 font-bold text-ink-primary"><Wrench size={15} style={{ color: accent }} /> {s.name}</span>
                  <span className="mt-0.5 block text-[13px] text-ink-tertiary">{[s.category, s.duration_min ? `${s.duration_min} min` : null].filter(Boolean).join(" · ")}</span>
                </span>
                <span className="whitespace-nowrap text-sm font-extrabold text-ink-primary">{money(s.price)}</span>
              </button>
            ))}
            <div className={"rounded-xl border p-4 " + (catchAll ? "border-2" : "border-dashed border-slate-300")} style={catchAll ? { borderColor: accent } : undefined}>
              <button onClick={() => setCatchAll(!catchAll)} className="flex w-full items-center gap-2 text-left font-bold text-ink-primary">
                <MessageSquare size={15} style={{ color: accent }} /> Something else / not sure
              </button>
              {catchAll && (
                <>
                  <textarea value={issue} onChange={(e) => setIssue(e.target.value)} rows={3}
                    className={input + " resize-y"} placeholder="Describe the problem — we'll figure out the rest…" autoFocus />
                  <button onClick={() => { setSvc(null); setEmpId(null); goTime(null, null); }} disabled={!issue.trim()}
                    className="mt-2.5 w-full rounded-lg py-2.5 text-sm font-bold text-white disabled:opacity-50" style={{ background: accent }}>
                    Pick a time
                  </button>
                </>
              )}
            </div>
          </div>
          <button onClick={() => { setFlash(null); setReqMode(true); }} className="mt-4 text-[13px] font-semibold text-ink-tertiary underline-offset-2 hover:underline">
            Can't find what you need? Leave a request instead
          </button>
        </>
      )}

      {/* ── specialist ── */}
      {stepK === "emp" && (
        <>
          <h2 className="text-lg font-extrabold text-ink-primary">Anyone in particular?</h2>
          <div className="mt-4 flex flex-col gap-2.5">
            <button onClick={() => { setEmpId(null); goTime(svc, null); }} className={cardBtn + " border-slate-200 flex items-center gap-3"}>
              <span className="grid h-9 w-9 place-items-center rounded-full" style={{ background: `${accent}1f`, color: accent }}><Sparkles size={16} /></span>
              <span><span className="font-bold text-ink-primary">No preference</span>
                <span className="block text-[13px] text-ink-tertiary">First available specialist</span></span>
            </button>
            {staffFor.map((e) => (
              <button key={e.id} onClick={() => { setEmpId(e.id); goTime(svc, e.id); }} className={cardBtn + " border-slate-200 flex items-center gap-3"}>
                <span className="grid h-9 w-9 place-items-center rounded-full text-sm font-extrabold text-white" style={{ background: accent }}>{e.name.slice(0, 1).toUpperCase()}</span>
                <span className="flex items-center gap-2 font-bold text-ink-primary"><User size={14} className="text-ink-tertiary" /> {e.name}</span>
              </button>
            ))}
          </div>
        </>
      )}

      {/* ── time ── */}
      {stepK === "time" && (
        <>
          <h2 className="text-lg font-extrabold text-ink-primary">Pick a time</h2>
          <div className="mt-1 text-[13px] text-ink-tertiary">
            {svc ? <>{svc.name} · {svc.duration_min || cfg.time_slot || 60} min · {money(svc.price)}</> : "We'll confirm the details when you arrive"}
          </div>
          {days === null && <div className="mt-6 flex items-center gap-2 text-sm text-ink-tertiary"><Loader2 size={16} className="animate-spin" /> Finding open slots…</div>}
          {days !== null && noAvailability && (
            <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
              <div className="font-bold text-ink-primary">No open slots in the next two weeks</div>
              <div className="mt-1 text-[13px] text-ink-secondary">Leave a request and {cfg.shop_name} will call to fit you in.</div>
              <button onClick={() => { setFlash(null); setReqMode(true); }} className="mt-3 rounded-lg px-4 py-2 text-sm font-bold text-white" style={{ background: accent }}>Leave a request</button>
            </div>
          )}
          {days !== null && !noAvailability && (
            <>
              <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
                {days.map((d) => {
                  const f = fmtDay(d.date); const sel = d.date === date; const off = !d.open || d.free === 0;
                  return (
                    <button key={d.date} disabled={off} onClick={() => pickDay(d.date)}
                      className={"min-w-[64px] rounded-xl border px-2 py-2 text-center " + (off ? "opacity-40 " : "") + "border-slate-200"}
                      style={sel ? { background: accent, borderColor: accent, color: "#fff" } : undefined}>
                      <div className={"text-[10px] font-bold uppercase " + (sel ? "" : "text-ink-tertiary")}>{f.dow}</div>
                      <div className="text-lg font-extrabold leading-tight">{f.num}</div>
                      <div className={"text-[10px] font-semibold " + (sel ? "" : "text-ink-tertiary")}>{off ? "—" : `${d.free} free`}</div>
                    </button>
                  );
                })}
              </div>
              {slots === null && date && <div className="mt-4 flex items-center gap-2 text-sm text-ink-tertiary"><Loader2 size={14} className="animate-spin" /> Loading times…</div>}
              {slots !== null && slots.length === 0 && <div className="mt-4 text-sm text-ink-tertiary">No times left this day — try another.</div>}
              {Object.entries(grouped).map(([g, list]) => (
                <div key={g} className="mt-4">
                  <div className="text-[11px] font-extrabold uppercase tracking-[.07em] text-ink-tertiary">{g}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {list.map((t) => (
                      <button key={t} onClick={() => setTime(t)} className="rounded-lg border px-4 py-2 text-sm font-bold"
                        style={time === t ? { background: accent, borderColor: accent, color: "#fff" } : { borderColor: "#c2c6d5", color: "#14213f" }}>
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <button onClick={() => { setFlash(null); setStepK("you"); }} disabled={!time}
                className={cta} style={{ background: accent }}>Continue</button>
            </>
          )}
        </>
      )}

      {/* ── details ── */}
      {stepK === "you" && (
        <>
          <h2 className="text-lg font-extrabold text-ink-primary">Almost done</h2>
          <div className="mt-1 text-[13px] text-ink-tertiary">
            {[svc?.name || (catchAll ? "Repair visit" : null), date && time ? `${date} at ${time}` : null].filter(Boolean).join(" · ")}
          </div>
          <label className={lbl}>Phone</label>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} onBlur={checkHello} inputMode="tel" className={input} placeholder="So we can confirm your appointment" autoFocus />
          {hello && <div className="mt-2 rounded-lg px-3 py-2 text-[13px] font-semibold" style={{ background: `${accent}14`, color: accent }}>Welcome back, {hello}!</div>}
          <label className={lbl}>Your name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} className={input} />
          <label className={lbl}>Email <span className="font-normal text-ink-tertiary">(optional)</span></label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" className={input} />
          <label className={lbl}>Anything we should know? <span className="font-normal text-ink-tertiary">(optional)</span></label>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={input + " resize-y"} />
          {flash && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] font-semibold text-rose-600">{flash}</div>}
          <button onClick={book} disabled={busy} className={cta} style={{ background: accent }}>
            {busy && <Loader2 size={16} className="animate-spin" />}{busy ? "Appointment…" : "Confirm appointment"}
          </button>
        </>
      )}
    </Shell>
  );
}

// ── page shell: brandhead + progress bar ──
function Shell({ accent, cfg, steps, at, onBack, children }: {
  accent: string; cfg?: Cfg | null; steps?: number; at?: number;
  onBack?: () => void; children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-surface font-sans text-ink-primary">
      <div className="mx-auto max-w-md px-4 py-7">
        <div className="mb-5 flex items-center gap-3">
          {onBack && (
            <button onClick={onBack} aria-label="Back" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50">
              <ChevronLeft size={17} />
            </button>
          )}
          {cfg?.logo_data
            ? <img src={cfg.logo_data} alt={cfg.shop_name} className="h-10 w-10 shrink-0 rounded-xl object-contain" style={{ background: "#fff" }} />
            : <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-[15px] font-extrabold text-white" style={{ background: accent }}>
                {(cfg?.shop_name || "R").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase()}
              </div>}
          <div className="min-w-0">
            <div className="truncate text-[15px] font-extrabold text-ink-primary">{cfg?.shop_name || "Book an appointment"}</div>
            <div className="text-xs font-semibold text-ink-tertiary">Book an appointment</div>
          </div>
        </div>
        {steps != null && at != null && (
          <div className="mb-5 flex gap-1.5">
            {Array.from({ length: steps }, (_, i) => (
              <div key={i} className="h-1 flex-1 rounded-full" style={{ background: i <= at ? accent : "#dde2ee" }} />
            ))}
          </div>
        )}
        <div className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">{children}</div>
        <div className="mt-6 text-center text-[11.5px] text-ink-tertiary">Powered by RepairOS</div>
      </div>
    </div>
  );
}
