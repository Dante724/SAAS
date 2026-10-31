// New job / New estimate — one screen, no wizard. Everything is optional, so a walk-in is one tap.
// Customer search (or "Add 'Maria 0412…'" inline) → their devices as chips → problem → the counter
// details (job type and the shop's own check-in questions, priority, promised-by date, a quote and
// a deposit) → photos → who's on it. On a tablet the form sits beside a live summary with the
// Create button, and the customer can sign the drop-off terms before the ticket is printed.
// Creating is online only with an Idempotency-Key, so a retried tap can't make two jobs; photos,
// the deposit and the signature follow once the job exists and never block it.
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { CalendarClock, Camera, Check, ChevronLeft, Loader2, PenLine, Plus, Search, Smartphone, User, UserPlus, X } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card, Page, PrimaryButton } from "../components/ui";
import IntakeFields, { intakeFieldsFrom } from "../components/IntakeFields";
import QuoteSheet from "../components/QuoteSheet";
import SignatureSheet from "../components/SignatureSheet";
import JobCreated from "../components/JobCreated";
import { addPhotosToJob, newKey, shrinkPhoto, uploadPhoto } from "../lib/photos";
import { addProblem, duePresets, missingFields, orderBody, quoteTotal, splitNamePhone, type IntakeField, type NewDevice, type QuoteLine } from "../lib/newJob";
import { PRIORITY_LABEL } from "../lib/queue";
import { money } from "../lib/pay";
import { inferRole, savedRole } from "../lib/role";
import { announceChange, useStaff, useToast, useWide } from "../lib/useGo";

type Device = { id: string; brand?: string | null; model?: string | null; serial?: string | null; imei?: string | null };
type Client = { id: string; name: string; phone?: string | null; devices?: Device[] };
type JobType = { id: string; name: string; is_default?: boolean };

const PROBLEMS = ["Cracked screen", "Battery", "Charging port", "Water damage", "Won't turn on", "Software"];
const CONDITIONS = ["Like new", "Scratches", "Dents", "Cracked back", "Bent frame"];
const deviceLabel = (d: Device) => [d.brand, d.model].filter(Boolean).join(" ") || d.serial || d.imei || "Device";
const field = "w-full min-h-[3rem] rounded-xl border border-slate-200 bg-white px-4 text-go-body text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15";
const chip = (on: boolean) => `min-h-[2.75rem] px-4 rounded-full border text-go-body font-medium inline-flex items-center gap-1.5 ${on ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`;
const whenText = (local: string) => new Date(local).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

function Label({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 px-1 text-go-sub font-semibold text-ink-tertiary">{children}</p>;
}

export default function NewJob() {
  const nav = useNavigate();
  const loc = useLocation();
  const toast = useToast();
  const wide = useWide();
  const { me, can } = useMe();
  const fmt = (n?: number | null) => money(me?.currency, n);
  const staff = useStaff();
  const mayEstimate = can("can_create_estimates");
  const mayQuote = can("can_add_services") || can("can_add_products");
  const [isEstimate, setIsEstimate] = useState(loc.pathname.endsWith("/estimate") && mayEstimate);

  // customer
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Client[]>([]);
  const [searching, setSearching] = useState(false);
  const [client, setClient] = useState<Client | null>(null);
  const [adding, setAdding] = useState<{ name: string; phone: string } | null>(null);
  const [savingClient, setSavingClient] = useState(false);
  const clientKey = useRef(newKey());
  // device
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [newDevice, setNewDevice] = useState<NewDevice | null>(null);
  const [condition, setCondition] = useState("");
  // problem, counter details, photos, who
  const [complaint, setComplaint] = useState("");
  const [priority, setPriority] = useState(0);
  const [dueAt, setDueAt] = useState("");
  const [pickDue, setPickDue] = useState(false);
  const [types, setTypes] = useState<JobType[]>([]);
  const [typeId, setTypeId] = useState<string | null>(null);
  const [fields, setFields] = useState<IntakeField[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [lines, setLines] = useState<QuoteLine[]>([]);
  const [quoteOpen, setQuoteOpen] = useState(false);
  const [deposit, setDeposit] = useState("");
  const [photos, setPhotos] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const role = savedRole() ?? inferRole(me);
  const [who, setWho] = useState<string>("");
  useEffect(() => { if (me?.id && role === "tech") setWho(me.id); }, [me?.id, role]);
  const mayAssign = can("can_assign_staff");
  // drop-off signature (on by default on a tablet at the counter)
  const [wantSign, setWantSign] = useState(wide);
  const [terms, setTerms] = useState<{ terms: string; shop: string } | null>(null);
  const [signFor, setSignFor] = useState<{ id: string; order_number: string } | null>(null);
  // create
  const [busy, setBusy] = useState<string | null>(null);
  const [created, setCreated] = useState<{ id: string; order_number: string } | null>(null);
  const jobKey = useRef(newKey());

  // job types and the chosen type's check-in questions
  useEffect(() => {
    api.get<{ items: JobType[] }>("/forms/types").then((r) => {
      const t = r.items || [];
      setTypes(t);
      setTypeId((cur) => cur ?? (t.find((x) => x.is_default) || t[0])?.id ?? null);
    }).catch(() => setTypes([]));
    api.get<{ terms: string; shop: string }>("/mobile/dropoff-terms").then(setTerms).catch(() => setTerms(null));
  }, []);
  useEffect(() => {
    if (!typeId) { setFields([]); return; }
    api.get<{ items: any[] }>(`/forms/layout?form=order&type_id=${encodeURIComponent(typeId)}`)
      .then((r) => setFields(intakeFieldsFrom(r.items || []))).catch(() => setFields([]));
  }, [typeId]);

  useEffect(() => {
    if (client || adding) return;
    const term = q.trim();
    if (term.length < 2) { setResults([]); return; }
    setSearching(true);
    const t = setTimeout(() => {
      api.get<{ items: Client[] }>(`/clients?search=${encodeURIComponent(term)}&limit=8&role=client`)
        .then((r) => setResults(r.items || [])).catch(() => setResults([])).finally(() => setSearching(false));
    }, 250);
    return () => clearTimeout(t);
  }, [q, client, adding]);

  const people = useMemo(() => {
    const base = [{ id: "", name: "Unassigned" }, { id: me?.id || "me", name: "Me" }];
    return mayAssign ? [...base, ...staff.filter((s) => s.id !== me?.id)] : base;
  }, [staff, me?.id, mayAssign]);

  function pickClient(c: Client) {
    setClient(c); setResults([]); setQ("");
    const devs = c.devices || [];
    setDeviceId(devs.length === 1 ? devs[0].id : null);
    setNewDevice(devs.length ? null : { brand: "", model: "", serial: "", imei: "" });
  }

  async function saveClient() {
    if (!adding?.name.trim() || savingClient) return;
    setSavingClient(true);
    try {
      const r = await api.postOnce<{ id: string; name: string }>("/clients", { name: adding.name.trim(), phone: adding.phone.trim() || null }, clientKey.current);
      pickClient({ id: r.id, name: r.name, phone: adding.phone, devices: [] });
      setAdding(null);
    } catch (e: any) {
      toast({ title: "Couldn't add the customer", body: e?.message, tone: "error" });
    } finally { setSavingClient(false); }
  }

  async function onPhotos(files: FileList | null) {
    if (!files?.length) return;
    for (const f of Array.from(files).slice(0, 12)) {
      try { const d = await shrinkPhoto(f); setPhotos((p) => [...p, d].slice(0, 12)); }
      catch (e: any) { toast({ title: "Couldn't use that photo", body: e?.message, tone: "error" }); }
    }
    if (fileRef.current) fileRef.current.value = "";
  }

  const depositAmount = Number(deposit.replace(",", "."));
  const total = quoteTotal(lines);

  async function create() {
    if (busy) return;
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Creating a job needs a connection so it gets its number.", tone: "warn" }); return; }
    const gaps = missingFields(fields, values);
    setMissing(new Set(gaps));
    if (gaps.length) { toast({ title: "A few check-in questions are needed", body: gaps.join(", "), tone: "warn" }); return; }
    setBusy("Creating…");
    try {
      const body = orderBody({
        isEstimate, clientId: client?.id, deviceId, newDevice, complaint, assignedToId: who === "me" ? me?.id : who || null,
        priority, dueAt: dueAt || null, typeId, customData: values, condition, lines,
      });
      const o = await api.postOnce<{ id: string; order_number: string }>("/orders", body, jobKey.current);
      if (photos.length) {
        setBusy(`Uploading photos 0/${photos.length}`);
        const ids: string[] = [];
        for (const [i, p] of photos.entries()) {
          try { ids.push(await uploadPhoto(p)); } catch { /* one bad upload doesn't lose the job */ }
          setBusy(`Uploading photos ${i + 1}/${photos.length}`);
        }
        if (ids.length) await addPhotosToJob(o.id, ids, "intake").catch(() => { /* silent: the job is already created; the photo count warning below covers the rest */ });
        if (ids.length < photos.length) toast({ title: `${photos.length - ids.length} photo(s) didn't upload`, body: "Add them again from the job.", tone: "warn" });
      }
      if (depositAmount > 0) {
        await api.patch(`/orders/${encodeURIComponent(o.id)}/deposit`, { amount: depositAmount })
          .catch((e: any) => toast({ title: "The deposit wasn't saved", body: e?.message, tone: "warn" }));
      }
      announceChange("jobs");
      setBusy(null);
      setCreated(o);
      if (wantSign && client) setSignFor(o);
    } catch (e: any) {
      toast({ title: `Couldn't create the ${isEstimate ? "estimate" : "job"}`, body: e?.message, tone: "error" });
      setBusy(null);
    }
  }

  async function saveDropoffSignature(png: string) {
    if (!signFor) return;
    await api.post(`/feed/order/${encodeURIComponent(signFor.id)}`, {
      item_type: "signature", visibility: "client",
      body: `Drop-off terms accepted by ${client?.name || "the customer"}:\n\n${terms?.terms || ""}`,
      media_data: png, media_name: "dropoff-signature.png", media_mime: "image/png",
    });
  }

  function reset() {
    setClient(null); setAdding(null); setQ(""); setDeviceId(null); setNewDevice(null); setCondition("");
    setComplaint(""); setPriority(0); setDueAt(""); setPickDue(false); setValues({}); setMissing(new Set());
    setLines([]); setDeposit(""); setPhotos([]); setCreated(null); setSignFor(null); setWantSign(wide);
    setWho(role === "tech" && me?.id ? me.id : "");
    jobKey.current = newKey(); clientKey.current = newKey();
  }

  const back = (
    <button onClick={() => (history.length > 1 ? nav(-1) : nav("/today"))} className="-ml-2 h-11 min-w-[2.75rem] pr-2 inline-flex items-center text-primary font-medium">
      <ChevronLeft size={26} />{created ? "Done" : "Cancel"}
    </button>
  );
  const term = q.trim();
  const split = splitNamePhone(term);

  if (created) {
    return (
      <Page title={isEstimate ? "Estimate booked in" : "Job booked in"} back={back}>
        <JobCreated job={created} isEstimate={isEstimate} customer={client}
          onOpen={() => nav(`/job/${encodeURIComponent(created.id)}`, { replace: true })} onAnother={reset} />
        <SignatureSheet open={!!signFor} onClose={() => setSignFor(null)} orderId={created.id} customer={client?.name}
          title="Drop-off terms" doneLabel="I agree" wide onSign={saveDropoffSignature}
          onSaved={() => toast({ title: "Signed", body: "Saved on the job and shown to the customer." })}
          intro={
            <div className="mb-3 rounded-xl border border-slate-200 bg-white p-4 text-left">
              <p className="text-go-sub font-semibold text-ink-tertiary">{terms?.shop || "Drop-off terms"} · {created.order_number}</p>
              <p className="mt-1.5 max-h-[18vh] overflow-y-auto whitespace-pre-wrap text-go-body leading-relaxed text-ink-primary">{terms?.terms || "Please sign to confirm you've left this item with us."}</p>
              <p className="mt-2 text-go-sub text-ink-secondary">{client?.name ? `${client.name}, please sign below.` : "Please sign below."}</p>
            </div>
          } />
      </Page>
    );
  }

  const presets = duePresets(new Date());

  const form = (
    <>
      {mayEstimate && (
        <div className="mb-5 grid grid-cols-2 rounded-xl bg-slate-100 p-1" role="tablist">
          {[false, true].map((est) => (
            <button key={String(est)} role="tab" aria-selected={isEstimate === est} onClick={() => setIsEstimate(est)}
              className={`min-h-[2.75rem] rounded-lg text-go-body font-semibold ${isEstimate === est ? "bg-white text-ink-primary" : "text-ink-secondary"}`}>
              {est ? "Estimate" : "Job"}
            </button>
          ))}
        </div>
      )}

      <section className="mb-5">
        <Label>Customer</Label>
        {client ? (
          <Card className="p-3 flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-primary"><User size={20} /></span>
            <div className="min-w-0 flex-1"><p className="font-semibold text-ink-primary truncate">{client.name}</p>{client.phone && <p className="text-go-sub text-ink-secondary">{client.phone}</p>}</div>
            <button onClick={() => { setClient(null); setDeviceId(null); setNewDevice(null); }} aria-label="Change customer" className="h-11 w-11 inline-flex items-center justify-center rounded-full text-ink-tertiary active:bg-slate-100"><X size={20} /></button>
          </Card>
        ) : adding ? (
          <Card className="p-3 space-y-2">
            <input autoFocus value={adding.name} onChange={(e) => setAdding({ ...adding, name: e.target.value })} placeholder="Name" aria-label="Name" className={field} />
            <input value={adding.phone} onChange={(e) => setAdding({ ...adding, phone: e.target.value })} placeholder="Phone" aria-label="Phone" inputMode="tel" className={field} />
            <div className="flex gap-2">
              <button onClick={() => setAdding(null)} className="min-h-[2.75rem] flex-1 rounded-xl border border-slate-200 font-semibold text-ink-primary">Back</button>
              <button onClick={saveClient} disabled={!adding.name.trim() || savingClient} className="min-h-[2.75rem] flex-1 rounded-xl bg-primary font-semibold text-white disabled:opacity-50">{savingClient ? "Adding…" : "Add customer"}</button>
            </div>
          </Card>
        ) : (
          <>
            <div className="relative">
              <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or phone" aria-label="Find a customer" autoComplete="off" enterKeyHint="search" className={`${field} pl-11`} />
              {searching && <Loader2 size={18} className="absolute right-4 top-1/2 -translate-y-1/2 animate-spin text-ink-tertiary" />}
            </div>
            {(results.length > 0 || term.length >= 2) && (
              <Card className="mt-2 overflow-hidden divide-y-[0.5px] divide-slate-200">
                {results.map((c) => (
                  <button key={c.id} onClick={() => pickClient(c)} className="w-full min-h-[3.25rem] px-4 py-2 text-left active:bg-slate-50">
                    <p className="font-medium text-ink-primary">{c.name}</p>
                    <p className="text-go-sub text-ink-secondary truncate">{[c.phone, ...(c.devices || []).slice(0, 2).map(deviceLabel)].filter(Boolean).join(" · ")}</p>
                  </button>
                ))}
                {term.length >= 2 && (
                  <button onClick={() => setAdding({ name: split.name, phone: split.phone })} className="w-full min-h-[3.25rem] px-4 flex items-center gap-2 text-left font-semibold text-primary active:bg-slate-50">
                    <UserPlus size={18} />Add “{term}”
                  </button>
                )}
              </Card>
            )}
            <p className="mt-2 px-1 text-go-sub text-ink-tertiary">Leave empty for a walk-in.</p>
          </>
        )}
      </section>

      {client && (
        <section className="mb-5">
          <Label>Device</Label>
          <div className="flex flex-wrap gap-2">
            {(client.devices || []).map((d) => (
              <button key={d.id} onClick={() => { setDeviceId(d.id); setNewDevice(null); }} className={chip(deviceId === d.id)}>
                {deviceId === d.id ? <Check size={16} /> : <Smartphone size={16} />}{deviceLabel(d)}
              </button>
            ))}
            <button onClick={() => { setDeviceId(null); setNewDevice(newDevice ?? { brand: "", model: "", serial: "", imei: "" }); }} className={chip(!!newDevice)}>
              <Plus size={16} />New device
            </button>
          </div>
          {newDevice && (
            <>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <input value={newDevice.brand} onChange={(e) => setNewDevice({ ...newDevice, brand: e.target.value })} placeholder="Brand" aria-label="Brand" className={field} />
                <input value={newDevice.model} onChange={(e) => setNewDevice({ ...newDevice, model: e.target.value })} placeholder="Model" aria-label="Model" className={field} />
                <input value={newDevice.imei} onChange={(e) => setNewDevice({ ...newDevice, imei: e.target.value })} placeholder="IMEI" aria-label="IMEI" inputMode="numeric" className={`${field} col-span-2 md:col-span-1`} />
                <input value={newDevice.serial} onChange={(e) => setNewDevice({ ...newDevice, serial: e.target.value })} placeholder="Serial number" aria-label="Serial number" autoCapitalize="characters" className={`${field} col-span-2 md:col-span-1`} />
              </div>
              <p className="mt-3 mb-2 px-1 text-go-sub font-semibold text-ink-tertiary">Condition at drop-off</p>
              <input value={condition} onChange={(e) => setCondition(e.target.value)} placeholder="Marks, cracks, missing parts…" aria-label="Condition at drop-off" className={field} />
              <div className="mt-2 flex flex-wrap gap-2">
                {CONDITIONS.map((c) => <button key={c} onClick={() => setCondition((cur) => addProblem(cur, c))} className={chip(false)}>{c}</button>)}
              </div>
            </>
          )}
        </section>
      )}

      <section className="mb-5">
        <Label>Problem</Label>
        <textarea value={complaint} onChange={(e) => setComplaint(e.target.value)} rows={3} placeholder="What's wrong?" aria-label="Problem"
          className={`${field} py-3 min-h-[5.5rem] resize-none`} />
        <div className="mt-2 flex flex-wrap gap-2">
          {PROBLEMS.map((p) => <button key={p} onClick={() => setComplaint((c) => addProblem(c, p))} className={chip(false)}>{p}</button>)}
        </div>
      </section>

      {types.length > 1 && (
        <section className="mb-5">
          <Label>Job type</Label>
          <div className="flex flex-wrap gap-2">
            {types.map((t) => <button key={t.id} onClick={() => { setTypeId(t.id); setMissing(new Set()); }} aria-pressed={typeId === t.id} className={chip(typeId === t.id)}>{typeId === t.id && <Check size={16} />}{t.name}</button>)}
          </div>
        </section>
      )}

      <section className="mb-5 grid gap-5 md:grid-cols-2">
        <div>
          <Label>Priority</Label>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Priority">
            {[0, 1, 2].map((p) => <button key={p} onClick={() => setPriority(p)} aria-pressed={priority === p} className={chip(priority === p)}>{priority === p && <Check size={16} />}{PRIORITY_LABEL[p]}</button>)}
          </div>
        </div>
        <div>
          <Label>Promised by</Label>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Promised by">
            {presets.map((p) => <button key={p.label} onClick={() => { setDueAt(dueAt === p.value ? "" : p.value); setPickDue(false); }} aria-pressed={dueAt === p.value} className={chip(dueAt === p.value)}>{dueAt === p.value && <Check size={16} />}{p.label}</button>)}
            <button onClick={() => setPickDue((v) => !v)} aria-pressed={pickDue} className={chip(pickDue || (!!dueAt && !presets.some((p) => p.value === dueAt)))}><CalendarClock size={16} />Pick…</button>
          </div>
          {pickDue && <input type="datetime-local" value={dueAt} onChange={(e) => setDueAt(e.target.value)} aria-label="Promised by date and time" className={`${field} mt-2`} />}
          {dueAt && <p className="mt-2 px-1 text-go-sub text-ink-secondary">Due {whenText(dueAt)}</p>}
        </div>
      </section>

      {fields.length > 0 && (
        <section className="mb-5">
          <Label>Check-in questions</Label>
          <IntakeFields fields={fields} values={values} missing={missing}
            onChange={(k, v) => { setValues((cur) => ({ ...cur, [k]: v })); setMissing((m) => { const n = new Set(m); const f = fields.find((x) => x.key === k); if (f) n.delete(f.label); return n; }); }} />
        </section>
      )}

      <section className="mb-5">
        <Label>{isEstimate ? "Estimate" : "Quote"}</Label>
        <Card className="p-3">
          {lines.length > 0 && (
            <div className="divide-y-[0.5px] divide-slate-200 mb-2">
              {lines.map((l, i) => (
                <div key={i} className="flex items-center gap-3 py-2">
                  <span className="min-w-0 flex-1 truncate text-ink-primary">{l.description}</span>
                  <span className="tabular-nums text-ink-secondary">{fmt(l.unit_price * l.qty)}</span>
                  <button onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))} aria-label={`Remove ${l.description}`} className="h-10 w-10 -mr-1 inline-flex items-center justify-center rounded-full text-ink-tertiary active:bg-slate-100"><X size={18} /></button>
                </div>
              ))}
              <div className="flex justify-between pt-2 font-semibold text-ink-primary"><span>Total</span><span className="tabular-nums">{fmt(total)}</span></div>
            </div>
          )}
          <button onClick={() => setQuoteOpen(true)} className="w-full min-h-[2.75rem] flex items-center gap-2 font-semibold text-primary">
            <Plus size={18} />{lines.length ? "Add another" : mayQuote ? "Add services, parts or a price" : "Add a price"}
          </button>
        </Card>
        <div className="mt-3 flex items-center gap-3">
          <label htmlFor="deposit" className="flex-1 px-1 text-go-body text-ink-secondary">Ask for a deposit</label>
          <input id="deposit" value={deposit} onChange={(e) => setDeposit(e.target.value)} placeholder="0" inputMode="decimal" className={`${field} !w-36 text-right tabular-nums`} />
        </div>
      </section>

      <section className="mb-5">
        <Label>Photos</Label>
        <div className="flex gap-2 overflow-x-auto pb-1">
          <button onClick={() => fileRef.current?.click()} className="h-20 w-20 shrink-0 rounded-xl border-2 border-dashed border-slate-300 inline-flex flex-col items-center justify-center gap-1 text-ink-secondary active:bg-slate-50">
            <Camera size={22} /><span className="text-go-caption font-medium">Add</span>
          </button>
          {photos.map((p, i) => (
            <div key={i} className="relative h-20 w-20 shrink-0">
              <img src={p} alt={`Photo ${i + 1}`} className="h-20 w-20 rounded-xl object-cover" />
              <button onClick={() => setPhotos((l) => l.filter((_, j) => j !== i))} aria-label="Remove photo"
                className="absolute -right-1.5 -top-1.5 h-7 w-7 rounded-full bg-ink-primary text-white inline-flex items-center justify-center"><X size={14} /></button>
            </div>
          ))}
        </div>
        <p className="mt-1.5 px-1 text-go-sub text-ink-tertiary">Saved as check-in photos.</p>
        <input ref={fileRef} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => onPhotos(e.target.files)} />
      </section>

      <section className="mb-6">
        <Label>Who's on it</Label>
        <div className="flex flex-wrap gap-2">
          {people.map((p) => {
            const id = p.id === "me" ? me?.id || "" : p.id;
            return <button key={p.id || "none"} onClick={() => setWho(id)} className={chip(who === id)}>{who === id && <Check size={16} />}{p.name}</button>;
          })}
        </div>
      </section>
    </>
  );

  const signToggle = client && (
    <button onClick={() => setWantSign((v) => !v)} aria-pressed={wantSign}
      className="w-full min-h-[3.25rem] flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 text-left">
      <span className={`h-6 w-6 shrink-0 rounded-md border-2 inline-flex items-center justify-center ${wantSign ? "border-primary bg-primary text-white" : "border-slate-300"}`}>{wantSign && <Check size={16} />}</span>
      <span className="flex-1 text-go-body text-ink-primary">Customer signs the drop-off terms</span>
      <PenLine size={18} className="text-ink-tertiary" />
    </button>
  );
  const createLabel = busy || (isEstimate ? "Create estimate" : "Create job");

  return (
    <Page title={isEstimate ? "New estimate" : "New job"} back={back}
      footer={!wide && <PrimaryButton onClick={create} disabled={!!busy}>{createLabel}</PrimaryButton>}>
      {wide ? (
        <div className="grid grid-cols-[minmax(0,1fr)_19rem] lg:grid-cols-[minmax(0,1fr)_22rem] gap-6 items-start">
          <div className="min-w-0">{form}</div>
          {/* tablet: a live summary of what's being booked in, with the Create button always in reach */}
          <aside className="sticky top-14 space-y-3" aria-label="Summary">
            <Card className="p-4 space-y-2.5 text-go-body">
              <p className="text-go-sub font-semibold text-ink-tertiary">Booking in</p>
              <Row k="Customer" v={client?.name || "Walk-in"} />
              <Row k="Device" v={deviceId ? deviceLabel((client?.devices || []).find((d) => d.id === deviceId) || { id: "" }) : newDevice && (newDevice.brand || newDevice.model) ? `${newDevice.brand} ${newDevice.model}`.trim() : "—"} />
              <Row k="Problem" v={complaint.trim() || "—"} />
              {types.length > 1 && <Row k="Type" v={types.find((t) => t.id === typeId)?.name || "—"} />}
              <Row k="Priority" v={PRIORITY_LABEL[priority]} warn={priority > 0} />
              <Row k="Promised by" v={dueAt ? whenText(dueAt) : "—"} />
              <Row k={isEstimate ? "Estimate" : "Quote"} v={lines.length ? fmt(total) : "—"} />
              {depositAmount > 0 && <Row k="Deposit" v={fmt(depositAmount)} />}
              <Row k="Photos" v={photos.length ? String(photos.length) : "—"} />
              {missing.size > 0 && <p className="rounded-xl bg-amber-50 px-3 py-2 text-go-sub text-amber-800">Still needed: {[...missing].join(", ")}</p>}
            </Card>
            {signToggle}
            <PrimaryButton onClick={create} disabled={!!busy}>{createLabel}</PrimaryButton>
            <p className="px-1 text-center text-go-sub text-ink-tertiary">Then print the ticket or text the tracking link.</p>
          </aside>
        </div>
      ) : (
        <>
          {form}
          {signToggle && <div className="mb-4">{signToggle}</div>}
        </>
      )}
      <QuoteSheet open={quoteOpen} onClose={() => setQuoteOpen(false)} deviceId={deviceId}
        onAdd={(l) => { setLines((ls) => [...ls, l]); toast({ title: `Added ${l.description}` }); }} />
      <span className="sr-only" aria-live="polite">{busy || ""}</span>
    </Page>
  );
}

function Row({ k, v, warn }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-ink-secondary shrink-0">{k}</span>
      <span className={`min-w-0 truncate text-right font-medium ${warn ? "text-rose-700" : "text-ink-primary"}`}>{v}</span>
    </div>
  );
}
