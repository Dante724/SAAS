import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Clock, Loader2 } from "lucide-react";
import ScanButton from "../components/ScanButton";
import { Field as Float, inputCls, selectCls, useDeviceTheme } from "./authUi";

// Self check-in (docs/SPEC_self_checkin.md). /c/<token> is a check-in point's QR: the form comes
// from the server (the shop's industry template, the job types this point offers and the fields
// ticked "Ask at check-in"), so nothing here is hard-coded per trade. /c/s/<token> is the
// customer's own place in the queue.

export type Field = {
  key: string; label: string; type: string; required?: boolean; placeholder?: string; options?: string[];
  show_when?: { key: string; op: string; value?: string } | null;
};
export type Form = {
  asset_label: string;
  groups: { key: string; label: string; identifier: { primary: string; allowed: string[] } }[];
  fields: Field[]; reading: { key: string; label: string } | null;
  job_types: { id: string; name: string; fields: Field[] }[]; device_fields: Field[];
  terms: string; require_photo: boolean; welcome_text: string;
};
type Page = {
  shop: { name: string; logo?: string | null; color?: string | null };
  location: { name: string; phone?: string | null }; point: { name: string; kiosk: boolean; closed?: boolean };
  mode: "walk_in" | "key_drop"; form: Form; waiting: number; wait_min: number | null;
};
type Done = { status_token: string; queue_no: number; kind: string; ahead: number; wait_min: number | null; texted: boolean };

const ID_LABEL: Record<string, string> = { vin: "VIN", hin: "HIN", imei: "IMEI", serial: "Serial number", engine_serial: "Engine serial" };
const ID_HINT: Record<string, string> = {
  vin: "On the windscreen or the driver's door frame", hin: "On the transom, right side",
  imei: "Dial *#06# or check the box", serial: "On the label or in settings", engine_serial: "On the engine plate",
};

/** Is a conditional field shown, given what's been answered so far (same rule as the server). */
export function visible(f: Field, values: Record<string, unknown>): boolean {
  const r = f.show_when;
  if (!r) return true;
  const cur = values[r.key];
  const empty = cur == null || (typeof cur === "string" && !cur.trim());
  if (r.op === "filled") return !empty;
  if (empty) return false;
  if (r.op === "is") return String(cur) === String(r.value);
  if (r.op === "is_not") return String(cur) !== String(r.value);
  const n = Number(cur), w = Number(r.value);
  if (Number.isNaN(n) || Number.isNaN(w)) return false;
  return r.op === "gt" ? n > w : r.op === "lt" ? n < w : true;
}

function readable(hex?: string | null) {
  const h = (hex || "").replace("#", "");
  if (h.length !== 6) return "#ffffff";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return r * 0.299 + g * 0.587 + b * 0.114 > 160 ? "#111827" : "#ffffff";
}
const initials = (s?: string) => (s || "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "•";

async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j?.detail === "string" ? j.detail : r.status === 429 ? "Too many tries. Wait a moment and try again." : "Couldn't check you in. Please ask at the counter.");
  return j as T;
}

// Same frame as the tracking page and portal: the shop's logo (or initials) tile, one column, a
// quiet Tekly footer. Every colour is a theme token, so the page follows the phone's light/dark.
function Shell({ shop, foot, children }: { shop?: Page["shop"]; foot?: string; children: React.ReactNode }) {
  const accent = shop?.color || "#0059ba";
  return (
    <div className="flex min-h-screen flex-col bg-canvas font-sans text-ink-primary">
      <div className="mx-auto w-full max-w-[480px] flex-1 px-4 pt-6 sm:pt-10">
        {shop && (
          <div className="flex items-center gap-3">
            {shop.logo
              ? <img src={shop.logo} alt="" className="h-9 w-9 shrink-0 rounded-[10px] bg-white object-contain" />
              : <div className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-[14px] font-semibold" style={{ background: accent, color: readable(accent) }}>{initials(shop.name)}</div>}
            <div className="min-w-0 truncate text-[15px] font-semibold">{shop.name}</div>
          </div>
        )}
        {children}
      </div>
      <footer className="px-4 py-6 text-center text-[12px] text-ink-tertiary">{foot ? `${foot} · ` : ""}Checked in with Tekly</footer>
    </div>
  );
}

const card = "rounded-2xl border border-slate-200 bg-white shadow-clay-card";
const H2 = ({ children }: { children: React.ReactNode }) => <h2 className="mb-3 text-[17px] font-semibold tracking-[-0.01em]">{children}</h2>;
const sec = "mt-6 border-t border-slate-200 pt-6";
const link = "text-[14px] font-medium text-ink-secondary underline-offset-2 hover:underline";

function Chips<T extends string>({ items, value, onPick, accent }: { items: { k: T; label: string }[]; value: string; onPick: (k: T) => void; accent: string }) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((x) => {
        const on = value === x.k;
        return (
          <button key={x.k} type="button" aria-pressed={on} onClick={() => onPick(x.k)}
            className={`h-11 rounded-full border px-4 text-[15px] font-medium transition ${on ? "shadow-clay-sm" : "border-slate-200 bg-white text-ink-primary hover:border-slate-400"}`}
            style={on ? { background: accent, color: readable(accent), borderColor: accent } : undefined}>{x.label}</button>
        );
      })}
    </div>
  );
}

function FieldInput({ f, value, onChange }: { f: Field; value: unknown; onChange: (v: unknown) => void }) {
  const id = `f-${f.key}`;
  const v = value == null ? "" : String(value);
  const label = `${f.label}${f.required ? "" : " (optional)"}`;
  if (f.type === "bool") {
    return <label className="flex items-center gap-3 text-[15px]"><input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} className="h-5 w-5" />{f.label}</label>;
  }
  if (f.type === "select") {
    return (
      <Float id={id} label={label} select right={<ChevronDown size={18} className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />}>
        <select id={id} value={v} onChange={(e) => onChange(e.target.value)} className={selectCls}>
          <option value="">Choose…</option>
          {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </Float>
    );
  }
  return (
    <Float id={id} label={label}>
      <input id={id} value={v} placeholder=" " inputMode={f.type === "number" ? "decimal" : undefined} type={f.type === "date" ? "date" : "text"}
        onChange={(e) => onChange(e.target.value)} className={inputCls} />
    </Float>
  );
}

function SignaturePad({ onChange }: { onChange: (png: string | null) => void }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const [inked, setInked] = useState(false);
  useEffect(() => {
    const c = ref.current!; c.width = c.offsetWidth || 320; c.height = 120;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.strokeStyle = "#18254a"; ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.lineJoin = "round";
    let drawing = false;
    const pos = (e: any) => { const r = c.getBoundingClientRect(); const t = e.touches ? e.touches[0] : e; return { x: (t.clientX - r.left) * (c.width / r.width), y: (t.clientY - r.top) * (c.height / r.height) }; };
    const start = (e: any) => { e.preventDefault(); drawing = true; setInked(true); const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); };
    const move = (e: any) => { if (!drawing) return; e.preventDefault(); const p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); };
    const end = () => { if (drawing) { drawing = false; onChange(c.toDataURL("image/png")); } };
    c.addEventListener("mousedown", start); c.addEventListener("mousemove", move); window.addEventListener("mouseup", end);
    c.addEventListener("touchstart", start, { passive: false }); c.addEventListener("touchmove", move, { passive: false }); c.addEventListener("touchend", end);
    return () => {
      c.removeEventListener("mousedown", start); c.removeEventListener("mousemove", move); window.removeEventListener("mouseup", end);
      c.removeEventListener("touchstart", start); c.removeEventListener("touchmove", move); c.removeEventListener("touchend", end);
    };
  }, [onChange]);
  function clear() { const c = ref.current!; c.getContext("2d")?.clearRect(0, 0, c.width, c.height); setInked(false); onChange(null); }
  return (
    // Paper-white in both themes: the ink stays readable and the saved signature prints cleanly.
    <div className="relative overflow-hidden rounded-xl border border-slate-300" style={{ background: "#ffffff" }}>
      <canvas ref={ref} aria-label="Sign here" className="block h-[120px] w-full touch-none" />
      {!inked && <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[15px]" style={{ color: "#94a3b8" }}>Sign here with your finger</span>}
      <div className="pointer-events-none absolute bottom-6 left-4 right-4 border-b" style={{ borderColor: "#e2e8f0" }} />
      {inked && <button type="button" onClick={clear} className="absolute right-2 top-2 rounded-md px-2 py-1 text-[13px] font-medium" style={{ color: "#475569" }}>Clear</button>}
    </div>
  );
}

function readPhoto(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(new Error("Couldn't read that photo"));
    fr.readAsDataURL(file);
  });
}

// The template's identity fields: asked up front only when there's no decoded code to fill them.
const CORE = new Set(["make", "brand", "model", "year"]);
const ME_KEY = "tekly.checkin.me";
const titleCase = (s?: string) => (s && s.length > 3 && s === s.toUpperCase() ? s.toLowerCase().replace(/(^|[\s-])\w/g, (m) => m.toUpperCase()) : s || "");

function WaitPill({ accent, children }: { accent: string; children: React.ReactNode }) {
  return <span className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-semibold" style={{ background: `${accent}1f`, color: accent }}><Clock size={14} />{children}</span>;
}

export default function PublicCheckin({ token }: { token: string }) {
  useDeviceTheme();
  const [page, setPage] = useState<Page | null>(null);
  const [gone, setGone] = useState<string | null>(null);
  const [jobType, setJobType] = useState<string>("");
  const [group, setGroup] = useState<string>("");
  const [ident, setIdent] = useState("");
  const [car, setCar] = useState<string>("");          // "2019 Honda Accord" once the code decodes
  const [hint, setHint] = useState<string>("");        // decode problem ("check this…")
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [reading, setReading] = useState("");
  const [issue, setIssue] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [remembered, setRemembered] = useState(false);
  const [keyTag, setKeyTag] = useState("");
  const [photos, setPhotos] = useState<string[]>([]);
  const [sig, setSig] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [moreYou, setMoreYou] = useState(false);
  const [terms, setTerms] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done | null>(null);

  function load() {
    fetch(`/api/public/checkin/${token}`).then(async (r) => {
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setGone(typeof j?.detail === "string" ? j.detail : "This code isn't used any more. Please ask at the counter."); return; }
      setPage(j);
      if (j.form.job_types.length === 1) setJobType(j.form.job_types[0].id);
      if (j.form.groups.length === 1) setGroup(j.form.groups[0].key);
      // A returning customer on their own phone: fill their details (never on a shared counter tablet).
      if (!j.point.kiosk) {
        try {
          const me = JSON.parse(localStorage.getItem(ME_KEY) || "null");
          if (me?.name) { setName(me.name); setPhone(me.phone || ""); setEmail(me.email || ""); setRemembered(true); }
        } catch { /* private mode */ }
      }
    }).catch(() => setGone("Couldn't load the check-in page. Please ask at the counter."));
  }
  useEffect(load, [token]);

  // Kiosk: after a check-in, show the number for 30 s, then clear for the next person.
  useEffect(() => {
    if (!done || !page?.point.kiosk) return;
    const t = setTimeout(() => window.location.reload(), 30000);
    return () => clearTimeout(t);
  }, [done, page]);

  if (gone) return <Shell><p role="alert" className={`${card} mt-8 p-5 text-[15px]`}>{gone}</p></Shell>;
  if (!page) return <Shell><div className="mt-16 flex justify-center text-ink-tertiary"><Loader2 className="animate-spin" /></div></Shell>;

  const { form, shop } = page;
  const accent = shop.color || "#0059ba";
  const onAccent = readable(accent);
  const keyDrop = page.mode === "key_drop";
  const g = form.groups.find((x) => x.key === group);
  const idKind = g?.identifier.primary || form.groups[0]?.identifier.primary || "serial";
  const idLabel = ID_LABEL[idKind] || "Serial number";
  const jt = form.job_types.find((t) => t.id === jobType);
  const asked = [...(jt?.fields || []), ...form.device_fields].filter((f) => visible(f, answers));
  const mustAsk = asked.filter((f) => f.required);
  const mayAsk = asked.filter((f) => !f.required);
  const core = form.fields.filter((f) => CORE.has(f.key));
  const extra = form.fields.filter((f) => !CORE.has(f.key));
  const setAnswer = (k: string, v: unknown) => setAnswers((a) => ({ ...a, [k]: v }));
  const asset = form.asset_label.toLowerCase();

  async function identify(value: string) {
    if (!value.trim()) { setCar(""); setHint(""); return; }
    try {
      const r = await post<{ make?: string; model?: string; year?: number; problem?: string | null }>(
        `/api/public/checkin/${token}/identify`, { value, kind: idKind });
      const make = titleCase(r.make);
      const label = [r.year, make, r.model].filter(Boolean).join(" ");
      setHint(r.problem ? `Check this: ${r.problem}` : "");
      setCar(r.problem ? "" : label);
      setAnswers((a) => ({
        ...a,
        ...(make && form.fields.some((f) => f.key === "make") ? { make } : {}),
        ...(make && form.fields.some((f) => f.key === "brand") ? { brand: make } : {}),
        ...(r.model ? { model: r.model } : {}),
        ...(r.year && form.fields.some((f) => f.key === "year") ? { year: String(r.year) } : {}),
      }));
    } catch { setCar(""); }
  }
  function changeCar() { setCar(""); setIdent(""); setHint(""); }

  async function addPhoto(files: FileList | null) {
    const f = files?.[0];
    if (!f) return;
    try { const d = await readPhoto(f); setPhotos((p) => [...p, d].slice(0, 3)); } catch (e: any) { setErr(e.message); }
  }

  function forget() {
    try { localStorage.removeItem(ME_KEY); } catch { /* ignore */ }
    setName(""); setPhone(""); setEmail(""); setRemembered(false);
  }

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    setErr(null);
    if (form.job_types.length > 1 && !jobType) return setErr("Choose what you need");
    if (!issue.trim()) return setErr("Tell us what's wrong");
    for (const f of mustAsk) if (answers[f.key] == null || answers[f.key] === "") return setErr(`${f.label} is required`);
    if (keyDrop && form.require_photo && !photos.length) return setErr("Add a photo of the dashboard");
    if (!name.trim()) return setErr("Enter your name");
    if (phone.replace(/\D/g, "").length < 7) return setErr("Enter a mobile number so the shop can reach you");
    if (!sig) return setErr("Sign to agree to the shop's terms");
    setBusy(true);
    try {
      const r = await post<Done>(`/api/public/checkin/${token}`, {
        name, phone, email: email || null, job_type_id: jobType || null, asset_group: group || null,
        identifier_kind: ident.trim() ? idKind : null, identifier: ident.trim() || null,
        // Signing is agreeing: the line above the pad says so, and the server keeps both.
        reading: reading.trim() ? Number(reading) : null, issue, answers, signature: sig, agree: true,
        photos, key_tag: keyTag || null,
      });
      if (page?.point.kiosk) { setDone(r); return; }
      try { localStorage.setItem(ME_KEY, JSON.stringify({ name, phone, email })); } catch { /* ignore */ }
      window.location.assign(`/c/s/${r.status_token}`);
    } catch (e: any) {
      setErr(e.message);
    } finally { setBusy(false); }
  }

  if (done) {
    return (
      <Shell shop={shop} foot={page.location.name}>
        <Confirmation accent={accent} title={`You're checked in, ${name.split(" ")[0]}`} kind={done.kind} queueNo={done.queue_no}
          line={done.ahead === 0 ? "You're next" : `${done.ahead} ahead of you`} wait={done.wait_min}
          qr={`/api/public/checkin/s/${done.status_token}/qr.svg`} qrNote="Scan this with your phone to follow your place." />
        <a href="/login" className="mt-6 block text-center text-[13px] text-ink-tertiary underline">Staff: exit kiosk</a>
      </Shell>
    );
  }

  const intro = form.welcome_text || (!keyDrop ? "Tell us what's going on and we'll text you when it's your turn."
    : page.point.closed ? `${page.location.name} is closed right now. Leave your details here and your keys in the drop box.`
      : "Leave your details here and your keys in the drop box.");

  return (
    <Shell shop={shop} foot={page.location.name}>
      <div className="mt-8">
        <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.02em]">{keyDrop ? "Dropping off keys" : "Check in"}</h1>
        <p className="mt-1.5 text-[15px] text-ink-tertiary">{intro}</p>
        {!keyDrop && page.wait_min ? (
          <div className="mt-3"><WaitPill accent={accent}>About {page.wait_min} min wait{page.waiting ? ` · ${page.waiting} ahead` : ""}</WaitPill></div>
        ) : null}
      </div>

      <form onSubmit={submit} className={`${card} mt-6 p-5 sm:p-6`} noValidate>
        {/* 1 · what's going on */}
        {form.job_types.length > 1 && (<>
          <H2>What do you need?</H2>
          <Chips items={form.job_types.map((t) => ({ k: t.id, label: t.name }))} value={jobType} onPick={setJobType} accent={accent} />
          <div className="h-5" />
        </>)}
        <label htmlFor="ci-issue" className={form.job_types.length > 1 ? "mb-2 block text-[15px] font-semibold" : "mb-3 block text-[17px] font-semibold tracking-[-0.01em]"}>What's wrong?</label>
        <textarea id="ci-issue" value={issue} onChange={(e) => setIssue(e.target.value)} rows={3}
          placeholder={jt ? `A few words about the ${jt.name.toLowerCase()} problem` : "A few words is plenty"}
          className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-[16px] text-ink-primary outline-none transition placeholder:text-ink-tertiary focus:border-primary focus:ring-[3px] focus:ring-primary/20" />
        {mustAsk.length > 0 && <div className="mt-3 space-y-3">{mustAsk.map((f) => <FieldInput key={f.key} f={f} value={answers[f.key]} onChange={(v) => setAnswer(f.key, v)} />)}</div>}

        {keyDrop && (
          <div className="mt-3 grid grid-cols-2 gap-3">
            <Float id="ci-tag" label="Key tag number"><input id="ci-tag" value={keyTag} onChange={(e) => setKeyTag(e.target.value)} placeholder=" " className={inputCls} /></Float>
            <label className="flex h-[58px] cursor-pointer items-center justify-center rounded-xl border border-slate-300 bg-white px-3 text-center text-[14px] font-medium">
              {photos.length ? `${photos.length} photo${photos.length > 1 ? "s" : ""} · add` : `Dashboard photo${form.require_photo ? "" : " (optional)"}`}
              <input id="ci-photo" type="file" accept="image/*" capture="environment" onChange={(e) => addPhoto(e.target.files)} className="sr-only" />
            </label>
          </div>
        )}

        {/* 2 · the vehicle / boat / device */}
        <div className={sec}>
          <H2>Your {asset}</H2>
          {car ? (
            <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full" style={{ background: `${accent}1f`, color: accent }}><Check size={18} /></div>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold leading-snug">{car}</div>
                <div className="truncate text-[13px] text-ink-tertiary">{idLabel} ···{ident.trim().slice(-4).toUpperCase()}</div>
              </div>
              <button type="button" onClick={changeCar} className="shrink-0 text-[14px] font-medium" style={{ color: accent }}>Change</button>
            </div>
          ) : (<>
            {form.groups.length > 1 && <div className="mb-3"><Chips items={form.groups.map((x) => ({ k: x.key, label: x.label }))} value={group} onPick={setGroup} accent={accent} /></div>}
            <div className="flex gap-2">
              <div className="flex-1">
                <Float id="ci-ident" label={`${idLabel} (optional)`}>
                  <input id="ci-ident" value={ident} onChange={(e) => setIdent(e.target.value)} onBlur={() => identify(ident)}
                    placeholder=" " autoCapitalize="characters" className={inputCls} />
                </Float>
              </div>
              <div className="flex h-[58px] items-center"><ScanButton onScan={(code) => { setIdent(code); identify(code); }} title={`Scan the ${idLabel}`} /></div>
            </div>
            <p className={`mt-1.5 px-1 text-[13px] ${hint ? "text-amber-700" : "text-ink-tertiary"}`}>{hint || `${ID_HINT[idKind] || ""}${ID_HINT[idKind] ? ". " : ""}No ${idLabel}? Just tell us below.`}</p>
            {core.length > 0 && <div className="mt-3 grid grid-cols-2 gap-3">{core.map((f) => <FieldInput key={f.key} f={f} value={answers[f.key]} onChange={(v) => setAnswer(f.key, v)} />)}</div>}
          </>)}

          {(extra.length > 0 || form.reading || mayAsk.length > 0) && (
            more ? (
              <div className="mt-3 grid grid-cols-2 gap-3">
                {extra.map((f) => <FieldInput key={f.key} f={f} value={answers[f.key]} onChange={(v) => setAnswer(f.key, v)} />)}
                {form.reading && (
                  <Float id="ci-reading" label={`${form.reading.label} (optional)`}>
                    <input id="ci-reading" value={reading} onChange={(e) => setReading(e.target.value)} inputMode="numeric" placeholder=" " className={inputCls} />
                  </Float>
                )}
                {mayAsk.map((f) => <div key={f.key} className="col-span-2"><FieldInput f={f} value={answers[f.key]} onChange={(v) => setAnswer(f.key, v)} /></div>)}
              </div>
            ) : (
              <button type="button" onClick={() => setMore(true)} className={`${link} mt-3`}>+ Add more details{form.reading ? ` (${[...extra.map((f) => f.label), form.reading.label].slice(0, 3).join(", ").toLowerCase()})` : ""}</button>
            )
          )}
        </div>

        {/* 3 · the customer */}
        <div className={sec}>
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <h2 className="text-[17px] font-semibold tracking-[-0.01em]">{remembered ? `Welcome back, ${name.split(" ")[0]}` : "Your details"}</h2>
            {remembered && <button type="button" onClick={forget} className={link}>Not you?</button>}
          </div>
          <div className="space-y-3">
            <Float id="ci-name" label="Your name"><input id="ci-name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder=" " className={inputCls} /></Float>
            <Float id="ci-phone" label="Mobile"><input id="ci-phone" value={phone} onChange={(e) => setPhone(e.target.value)} type="tel" inputMode="tel" autoComplete="tel" placeholder=" " className={inputCls} /></Float>
            <p className="-mt-1 px-1 text-[13px] text-ink-tertiary">Only to text you about this visit.</p>
            {moreYou || email ? (
              <Float id="ci-email" label="Email (optional)"><input id="ci-email" value={email} onChange={(e) => setEmail(e.target.value)} type="email" autoComplete="email" placeholder=" " className={inputCls} /></Float>
            ) : <button type="button" onClick={() => setMoreYou(true)} className={link}>+ Add email</button>}
          </div>
        </div>

        {/* 4 · sign */}
        <div className={sec}>
          <H2>Sign</H2>
          <SignaturePad onChange={setSig} />
          <p className="mt-2 px-1 text-[13px] text-ink-tertiary">
            By signing you agree to the{" "}
            <button type="button" onClick={() => setTerms((x) => !x)} aria-expanded={terms} className="font-medium text-ink-secondary underline">shop's terms</button>.
          </p>
          {terms && <div className="mt-2 max-h-48 overflow-y-auto whitespace-pre-line rounded-xl bg-slate-50 p-4 text-[13px] text-ink-secondary">{form.terms}</div>}
        </div>

        {/* the button stays in reach while scrolling on a phone */}
        <div className="sticky bottom-0 -mx-5 mt-6 rounded-b-2xl bg-white/90 px-5 pb-[max(16px,env(safe-area-inset-bottom))] pt-3 backdrop-blur sm:-mx-6 sm:px-6">
          {err && <p role="alert" className="mb-3 rounded-xl bg-rose-50 px-4 py-2.5 text-[14px] text-rose-700">{err}</p>}
          <button type="submit" disabled={busy}
            className="flex h-[52px] w-full items-center justify-center gap-2 rounded-xl text-[16px] font-semibold shadow-clay-sm transition active:scale-[.995] disabled:opacity-60"
            style={{ background: accent, color: onAccent }}>
            {busy && <Loader2 size={18} className="animate-spin" />}
            {busy ? "Checking in…" : keyDrop ? "Leave my keys" : "Check in"}
          </button>
        </div>
      </form>
      {page.point.kiosk && <a href="/login" className="mt-6 block text-center text-[13px] text-ink-tertiary underline">Staff: exit kiosk</a>}
    </Shell>
  );
}

function Confirmation({ accent, title, kind, queueNo, line, wait, qr, qrNote, rows }: {
  accent: string; title: string; kind: string; queueNo?: number; line?: string; wait?: number | null;
  qr?: string; qrNote?: string; rows?: [string, string][];
}) {
  return (
    <div className={`${card} mt-8 p-6 text-center`}>
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full" style={{ background: `${accent}1f`, color: accent }}><Check size={28} /></div>
      <h1 className="mt-4 text-[22px] font-semibold leading-tight tracking-[-0.02em]">{title}</h1>
      {kind === "walk_in" && queueNo != null ? (<>
        <div className="mt-4 text-[64px] font-semibold leading-none tracking-[-0.03em] tabular-nums" style={{ color: accent }}>#{queueNo}</div>
        {line && <div className="mt-3"><WaitPill accent={accent}>{line}{wait ? ` · about ${wait} min` : ""}</WaitPill></div>}
      </>) : kind === "key_drop" ? <p className="mt-2 text-[15px] text-ink-tertiary">We've got your keys and details. We'll be in touch once we've looked at it.</p> : null}
      {rows && rows.length > 0 && (
        <dl className="mt-6 divide-y divide-slate-200 border-y border-slate-200 text-left text-[14px]">
          {rows.map(([k, v]) => <div key={k} className="flex justify-between gap-4 py-2.5"><dt className="text-ink-tertiary">{k}</dt><dd className="text-right font-medium">{v}</dd></div>)}
        </dl>
      )}
      {qr && (<>
        <div className="mx-auto mt-6 w-fit rounded-2xl border border-slate-200 p-3 shadow-clay-sm" style={{ background: "#ffffff" }}>
          <img src={qr} alt="Your check-in code" className="h-36 w-36" />
        </div>
        {qrNote && <p className="mt-3 text-[13px] text-ink-tertiary">{qrNote}</p>}
      </>)}
    </div>
  );
}

type Status = {
  first_name: string; queue_no: number; state: string; kind: string; ahead: number; wait_min: number | null;
  shop: Page["shop"]; location: string; summary: { asset: string; issue: string }; code: string; track_url: string | null;
};

export function PublicCheckinStatus({ token }: { token: string }) {
  useDeviceTheme();
  const [s, setS] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => fetch(`/api/public/checkin/s/${token}`).then(async (r) => {
      if (!r.ok) { if (live) setErr("We couldn't find this check-in."); return; }
      const j = await r.json(); if (live) setS(j);
    }).catch(() => {});
    load();
    const t = setInterval(load, 20000);
    return () => { live = false; clearInterval(t); };
  }, [token]);

  async function leave() {
    await fetch(`/api/public/checkin/s/${token}/cancel`, { method: "POST" }).catch(() => {});
    setS((x) => (x ? { ...x, state: "cancelled" } : x));
  }

  if (err) return <Shell><p role="alert" className={`${card} mt-8 p-5 text-[15px]`}>{err}</p></Shell>;
  if (!s) return <Shell><div className="mt-16 flex justify-center text-ink-tertiary"><Loader2 className="animate-spin" /></div></Shell>;
  const accent = s.shop?.color || "#0059ba";
  const head = s.state === "called" ? "You're up. Please come to the counter."
    : s.state === "converted" ? "You're being looked after."
    : s.state === "cancelled" ? "You've left the queue."
    : s.state === "no_show" ? "We missed you. Please ask at the counter."
    : s.kind === "key_drop" ? "We've got your keys" : `You're checked in, ${s.first_name}`;
  const live = s.state === "waiting" || s.state === "called";
  const rows: [string, string][] = [];
  if (s.summary.asset) rows.push(["Item", titleCase(s.summary.asset.split(" ").map((w) => titleCase(w)).join(" "))]);
  if (s.summary.issue) rows.push(["Issue", s.summary.issue]);
  return (
    <Shell shop={s.shop} foot={s.location}>
      <Confirmation accent={accent} title={head} kind={s.state === "waiting" ? s.kind : ""} queueNo={s.queue_no}
        line={s.ahead === 0 ? "You're next" : `${s.ahead} ahead of you`} wait={s.wait_min} rows={rows}
        qr={live ? `/api/public/checkin/s/${token}/qr.svg` : undefined}
        qrNote="Show this at the counter. This page updates by itself, so keep it open." />
      {s.track_url && <a href={s.track_url} className="mt-4 flex h-[52px] items-center justify-center rounded-xl text-[16px] font-semibold shadow-clay-sm" style={{ background: accent, color: readable(accent) }}>Follow your job</a>}
      {s.state === "waiting" && <button type="button" onClick={leave} className="mt-5 w-full text-center text-[13px] text-ink-tertiary underline">I can't wait, take me out of the queue</button>}
    </Shell>
  );
}
