import { useEffect, useRef, useState } from "react";
import ScanButton from "../components/ScanButton";

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

function useDeviceTheme() {
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const set = () => document.documentElement.classList.toggle("dark", !!mq?.matches);
    set(); mq?.addEventListener?.("change", set);
    return () => mq?.removeEventListener?.("change", set);
  }, []);
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j?.detail === "string" ? j.detail : r.status === 429 ? "Too many tries. Wait a moment and try again." : "Couldn't check you in. Please ask at the counter.");
  return j as T;
}

function Shell({ shop, children }: { shop?: Page["shop"]; children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-canvas text-ink-primary font-sans">
      <div className="mx-auto w-full max-w-md px-4 py-6">
        {shop && (
          <div className="mb-4 flex items-center gap-3">
            {shop.logo ? <img src={shop.logo} alt="" className="h-10 w-10 rounded-lg object-contain" /> : null}
            <div className="text-[17px] font-semibold">{shop.name}</div>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

const input = "w-full h-11 rounded-[10px] border border-slate-300 bg-white px-3 text-[15px] text-ink-primary dark:bg-slate-900";
const label = "block text-[13px] font-medium text-ink-secondary mb-1";
const chip = "h-10 rounded-full border border-slate-300 bg-white px-4 text-[14px] font-medium text-ink-primary dark:border-slate-600 dark:bg-slate-900";

/** One step of the form as a card, so a long template reads as four short parts. */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white/70 p-4 dark:border-slate-700 dark:bg-white/5">
      <h2 className="mb-3 text-[15px] font-semibold">{title}</h2>
      <div className="grid grid-cols-2 gap-x-3 gap-y-4">{children}</div>
    </section>
  );
}
const wide = "col-span-2";

function FieldInput({ f, value, onChange }: { f: Field; value: unknown; onChange: (v: unknown) => void }) {
  const id = `f-${f.key}`;
  const v = value == null ? "" : String(value);
  // Short answers (make, year, plate…) sit two to a row; free text and long labels take the full width.
  const full = f.type === "textarea" || f.type === "bool" || f.label.length > 22;
  return (
    <div className={full ? wide : undefined}>
      <label htmlFor={id} className={label}>{f.label}{f.required ? " *" : ""}</label>
      {f.type === "select" ? (
        <select id={id} value={v} onChange={(e) => onChange(e.target.value)} className={input}>
          <option value="">Choose…</option>
          {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : f.type === "bool" ? (
        <label className="flex items-center gap-2 text-[15px]"><input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />Yes</label>
      ) : f.type === "textarea" ? (
        <textarea id={id} value={v} onChange={(e) => onChange(e.target.value)} placeholder={f.placeholder} className={`${input} h-20 py-2`} />
      ) : (
        <input id={id} value={v} inputMode={f.type === "number" ? "decimal" : undefined} type={f.type === "date" ? "date" : "text"}
          onChange={(e) => onChange(e.target.value)} placeholder={f.placeholder} className={input} />
      )}
    </div>
  );
}

function SignaturePad({ onChange }: { onChange: (png: string | null) => void }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const c = ref.current!; c.width = c.offsetWidth || 320; c.height = 140;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.strokeStyle = "#18254a"; ctx.lineWidth = 2.2; ctx.lineCap = "round";
    let drawing = false;
    const pos = (e: any) => { const r = c.getBoundingClientRect(); const t = e.touches ? e.touches[0] : e; return { x: (t.clientX - r.left) * (c.width / r.width), y: (t.clientY - r.top) * (c.height / r.height) }; };
    const start = (e: any) => { e.preventDefault(); drawing = true; const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); };
    const move = (e: any) => { if (!drawing) return; e.preventDefault(); const p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); };
    const end = () => { if (drawing) { drawing = false; onChange(c.toDataURL("image/png")); } };
    c.addEventListener("mousedown", start); c.addEventListener("mousemove", move); window.addEventListener("mouseup", end);
    c.addEventListener("touchstart", start, { passive: false }); c.addEventListener("touchmove", move, { passive: false }); c.addEventListener("touchend", end);
    return () => {
      c.removeEventListener("mousedown", start); c.removeEventListener("mousemove", move); window.removeEventListener("mouseup", end);
      c.removeEventListener("touchstart", start); c.removeEventListener("touchmove", move); c.removeEventListener("touchend", end);
    };
  }, [onChange]);
  function clear() { const c = ref.current!; c.getContext("2d")?.clearRect(0, 0, c.width, c.height); onChange(null); }
  return (
    <div>
      {/* Always white, like paper: dark ink stays visible in dark mode and the saved signature has a white background. */}
      <div className="mb-1 text-[13px] font-medium text-ink-secondary">Sign with your finger</div>
      <canvas ref={ref} aria-label="Sign here" style={{ background: "#ffffff" }} className="h-[140px] w-full touch-none rounded-lg border-[1.5px] border-dashed border-slate-400" />
      <button type="button" onClick={clear} className="mt-1 text-[13px] font-medium text-ink-secondary underline">Clear signature</button>
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

export default function PublicCheckin({ token }: { token: string }) {
  useDeviceTheme();
  const [page, setPage] = useState<Page | null>(null);
  const [gone, setGone] = useState<string | null>(null);
  const [jobType, setJobType] = useState<string>("");
  const [group, setGroup] = useState<string>("");
  const [ident, setIdent] = useState("");
  const [decoded, setDecoded] = useState<string>("");
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [reading, setReading] = useState("");
  const [issue, setIssue] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [keyTag, setKeyTag] = useState("");
  const [photos, setPhotos] = useState<string[]>([]);
  const [sig, setSig] = useState<string | null>(null);
  const [agree, setAgree] = useState(false);
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
    }).catch(() => setGone("Couldn't load the check-in page. Please ask at the counter."));
  }
  useEffect(load, [token]);

  // Kiosk: after a check-in, show the number for 30 s, then clear for the next person.
  useEffect(() => {
    if (!done || !page?.point.kiosk) return;
    const t = setTimeout(() => window.location.reload(), 30000);
    return () => clearTimeout(t);
  }, [done, page]);

  if (gone) return <Shell><p role="alert" className="rounded-xl border border-slate-200 bg-white p-4 text-[15px]">{gone}</p></Shell>;
  if (!page) return <Shell><p className="text-ink-tertiary">Loading…</p></Shell>;

  const { form, shop } = page;
  const accent = shop.color || "#2563eb";
  const onAccent = readable(shop.color);
  const keyDrop = page.mode === "key_drop";
  const g = form.groups.find((x) => x.key === group);
  const idKind = g?.identifier.primary || form.groups[0]?.identifier.primary || "serial";
  const jt = form.job_types.find((t) => t.id === jobType);
  const values = { ...answers };
  const extraFields = [...(jt?.fields || []), ...form.device_fields].filter((f) => visible(f, values));
  const setAnswer = (k: string, v: unknown) => setAnswers((a) => ({ ...a, [k]: v }));

  async function identify(value: string) {
    if (!value.trim()) { setDecoded(""); return; }
    try {
      const r = await post<{ make?: string; model?: string; year?: number; problem?: string | null }>(
        `/api/public/checkin/${token}/identify`, { value, kind: idKind });
      const label = [r.year, r.make, r.model].filter(Boolean).join(" ");
      setDecoded(r.problem ? `Check this: ${r.problem}` : label ? `${label}, found from ${ID_LABEL[idKind] || "the code"}` : "");
      setAnswers((a) => ({
        ...a,
        ...(r.make && form.fields.some((f) => f.key === "make") && !a.make ? { make: r.make } : {}),
        ...(r.make && form.fields.some((f) => f.key === "brand") && !a.brand ? { brand: r.make } : {}),
        ...(r.model && !a.model ? { model: r.model } : {}),
        ...(r.year && form.fields.some((f) => f.key === "year") && !a.year ? { year: String(r.year) } : {}),
      }));
    } catch { setDecoded(""); }
  }

  async function addPhoto(files: FileList | null) {
    const f = files?.[0];
    if (!f) return;
    try { const d = await readPhoto(f); setPhotos((p) => [...p, d].slice(0, 3)); } catch (e: any) { setErr(e.message); }
  }

  async function submit() {
    setErr(null);
    if (form.job_types.length > 1 && !jobType) return setErr("Choose what you need");
    if (!issue.trim()) return setErr("Tell us what's wrong");
    if (!name.trim()) return setErr("Enter your name");
    if (phone.replace(/\D/g, "").length < 7) return setErr("Enter a mobile number so the shop can reach you");
    for (const f of extraFields) if (f.required && (answers[f.key] == null || answers[f.key] === "")) return setErr(`${f.label} is required`);
    if (keyDrop && form.require_photo && !photos.length) return setErr("Add a photo of the dashboard");
    if (!sig || !agree) return setErr("Sign and tick the box to agree to the shop's terms");
    setBusy(true);
    try {
      const r = await post<Done>(`/api/public/checkin/${token}`, {
        name, phone, email: email || null, job_type_id: jobType || null, asset_group: group || null,
        identifier_kind: ident.trim() ? idKind : null, identifier: ident.trim() || null,
        reading: reading.trim() ? Number(reading) : null, issue, answers, signature: sig, agree,
        photos, key_tag: keyTag || null,
      });
      if (page?.point.kiosk) setDone(r);
      else window.location.assign(`/c/s/${r.status_token}`);
    } catch (e: any) {
      setErr(e.message);
    } finally { setBusy(false); }
  }

  if (done) {
    return (
      <Shell shop={shop}>
        <div className="rounded-2xl border border-slate-200 bg-white p-6 text-center dark:bg-slate-900">
          <div className="text-[17px] font-semibold">You're checked in, {name.split(" ")[0]}</div>
          {done.kind === "walk_in" ? (<>
            <div className="mt-3 text-[48px] font-semibold leading-none">#{done.queue_no}</div>
            <div className="mt-1 text-ink-secondary">{done.ahead === 0 ? "You're next" : `${done.ahead} ahead of you`}{done.wait_min ? ` · about ${done.wait_min} min` : ""}</div>
          </>) : <div className="mt-3 text-ink-secondary">We've got your keys and details. We'll be in touch once we've looked at it.</div>}
          <img src={`/api/public/checkin/s/${done.status_token}/qr.svg`} alt="Your check-in code" className="mx-auto mt-4 h-36 w-36" />
          <p className="mt-2 text-[13px] text-ink-tertiary">Scan this with your phone to follow your place.</p>
        </div>
        <a href="/login" className="mt-6 block text-center text-[13px] text-ink-tertiary underline">Staff: exit kiosk</a>
      </Shell>
    );
  }

  const intro = form.welcome_text || (!keyDrop ? `${page.location.name} · We'll text you when it's your turn.`
    : page.point.closed ? `${page.location.name} is closed right now. Leave your details here and your keys in the drop box.`
      : "Leave your details here and your keys in the drop box.");

  return (
    <Shell shop={shop}>
      <h1 className="text-[22px] font-semibold">{keyDrop ? "Dropping off keys" : "Check in"}</h1>
      <p className="mt-1 text-[14px] text-ink-secondary">
        {intro}
        {!keyDrop && page.wait_min ? ` About ${page.wait_min} min wait.` : ""}
      </p>

      <div className="mt-5 space-y-4">
        <Section title={`Your ${form.asset_label.toLowerCase()}`}>
          {form.groups.length > 1 && (
            <fieldset className={wide}>
              <legend className={label}>What are we looking at?</legend>
              <div className="flex flex-wrap gap-2">
                {form.groups.map((x) => (
                  <button key={x.key} type="button" aria-pressed={group === x.key} onClick={() => setGroup(x.key)} className={chip}
                    style={group === x.key ? { background: accent, color: onAccent, borderColor: accent } : undefined}>{x.label}</button>
                ))}
              </div>
            </fieldset>
          )}

          <div className={wide}>
            <label htmlFor="ci-ident" className={label}>{ID_LABEL[idKind] || "Serial number"}</label>
            <div className="flex gap-2">
              <input id="ci-ident" value={ident} onChange={(e) => setIdent(e.target.value)} onBlur={() => identify(ident)}
                className={input} autoCapitalize="characters" />
              <ScanButton onScan={(code) => { setIdent(code); identify(code); }} title={`Scan the ${ID_LABEL[idKind] || "code"}`} />
            </div>
            <div className="mt-1 text-[12px] text-ink-tertiary">{decoded || ID_HINT[idKind] || ""}</div>
          </div>

          {form.fields.map((f) => <FieldInput key={f.key} f={f} value={answers[f.key]} onChange={(v) => setAnswer(f.key, v)} />)}

          {form.reading && (
            <div>
              <label htmlFor="ci-reading" className={label}>{form.reading.label}</label>
              <input id="ci-reading" value={reading} onChange={(e) => setReading(e.target.value)} inputMode="numeric" className={input} />
            </div>
          )}
        </Section>

        <Section title="What's going on">
          {form.job_types.length > 1 && (
            <fieldset className={wide}>
              <legend className={label}>What do you need? *</legend>
              <div className="flex flex-wrap gap-2">
                {form.job_types.map((t) => (
                  <button key={t.id} type="button" aria-pressed={jobType === t.id} onClick={() => setJobType(t.id)} className={chip}
                    style={jobType === t.id ? { background: accent, color: onAccent, borderColor: accent } : undefined}>{t.name}</button>
                ))}
              </div>
            </fieldset>
          )}

          {extraFields.map((f) => <FieldInput key={f.key} f={f} value={answers[f.key]} onChange={(v) => setAnswer(f.key, v)} />)}

          <div className={wide}>
            <label htmlFor="ci-issue" className={label}>What's wrong? *</label>
            <textarea id="ci-issue" value={issue} onChange={(e) => setIssue(e.target.value)} className={`${input} h-24 py-2`} />
          </div>

          {keyDrop && (<>
            <div>
              <label htmlFor="ci-tag" className={label}>Key tag number</label>
              <input id="ci-tag" value={keyTag} onChange={(e) => setKeyTag(e.target.value)} className={input} />
            </div>
            <div className={wide}>
              <div className={label}>Photo of the dashboard{form.require_photo ? " *" : ""}</div>
              <label className="inline-flex h-11 cursor-pointer items-center rounded-[10px] border border-slate-300 bg-white px-4 text-[14px] font-medium dark:border-slate-600 dark:bg-slate-900">
                {photos.length ? "Add another photo" : "Take a photo"}
                <input id="ci-photo" type="file" accept="image/*" capture="environment" onChange={(e) => addPhoto(e.target.files)} className="sr-only" />
              </label>
              {photos.length > 0 && <div className="mt-1 text-[12px] text-ink-tertiary">{photos.length} photo{photos.length > 1 ? "s" : ""} added</div>}
            </div>
          </>)}
        </Section>

        <Section title="About you">
          <div className={wide}>
            <label htmlFor="ci-name" className={label}>Your name *</label>
            <input id="ci-name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" className={input} />
          </div>
          <div className={wide}>
            <label htmlFor="ci-phone" className={label}>Mobile *</label>
            <input id="ci-phone" value={phone} onChange={(e) => setPhone(e.target.value)} type="tel" autoComplete="tel" className={input} />
          </div>
          <div className={wide}>
            <label htmlFor="ci-email" className={label}>Email</label>
            <input id="ci-email" value={email} onChange={(e) => setEmail(e.target.value)} type="email" autoComplete="email" className={input} />
          </div>
        </Section>

        <Section title="Agree and sign">
          <div className={wide}>
            <div className="max-h-32 overflow-y-auto whitespace-pre-line rounded-lg border border-slate-200 bg-white p-3 text-[13px] text-ink-secondary dark:border-slate-700 dark:bg-slate-900">{form.terms}</div>
            <div className="mt-3"><SignaturePad onChange={setSig} /></div>
            <label className="mt-3 flex items-start gap-2.5 text-[15px]"><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-0.5 h-5 w-5 flex-shrink-0" />I agree to the shop's terms</label>
          </div>
        </Section>

        {err && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-[14px] text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">{err}</p>}
        <button type="button" onClick={submit} disabled={busy}
          className="h-12 w-full rounded-xl text-[16px] font-semibold disabled:opacity-60" style={{ background: accent, color: onAccent }}>
          {busy ? "Checking in…" : keyDrop ? "Leave my keys" : "Check in"}
        </button>
        {page.point.kiosk && <a href="/login" className="block text-center text-[13px] text-ink-tertiary underline">Staff: exit kiosk</a>}
      </div>
    </Shell>
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

  if (err) return <Shell><p role="alert">{err}</p></Shell>;
  if (!s) return <Shell><p className="text-ink-tertiary">Loading…</p></Shell>;
  const head = s.state === "called" ? "You're up. Please come to the counter."
    : s.state === "converted" ? "You're being looked after."
    : s.state === "cancelled" ? "You've left the queue."
    : s.state === "no_show" ? "We missed you. Please ask at the counter."
    : s.kind === "key_drop" ? "We've got your keys and details." : `You're checked in, ${s.first_name}`;
  return (
    <Shell shop={s.shop}>
      <div className="rounded-2xl border border-slate-200 bg-white p-6 text-center dark:bg-slate-900">
        <div className="text-[17px] font-semibold">{head}</div>
        {s.state === "waiting" && s.kind === "walk_in" && (<>
          <div className="mt-3 text-[48px] font-semibold leading-none">#{s.queue_no}</div>
          <div className="mt-1 text-ink-secondary">{s.ahead === 0 ? "You're next" : `${s.ahead} ahead of you`}{s.wait_min ? ` · about ${s.wait_min} min` : ""}</div>
        </>)}
        <div className="mt-4 space-y-1 text-left text-[14px]">
          {s.summary.asset && <div className="flex justify-between gap-3"><span className="text-ink-tertiary">Item</span><span>{s.summary.asset}</span></div>}
          {s.summary.issue && <div className="flex justify-between gap-3"><span className="text-ink-tertiary">Issue</span><span className="text-right">{s.summary.issue}</span></div>}
        </div>
        {(s.state === "waiting" || s.state === "called") && (<>
          <img src={`/api/public/checkin/s/${token}/qr.svg`} alt="Your check-in code" className="mx-auto mt-4 h-36 w-36" />
          <p className="mt-1 text-[13px] text-ink-tertiary">Show this at the counter. Save this page to follow your place.</p>
        </>)}
        {s.track_url && <a href={s.track_url} className="mt-4 inline-block font-semibold underline">Follow your job</a>}
      </div>
      {s.state === "waiting" && <button type="button" onClick={leave} className="mt-4 w-full text-center text-[14px] text-ink-secondary underline">I can't wait, take me out of the queue</button>}
    </Shell>
  );
}
