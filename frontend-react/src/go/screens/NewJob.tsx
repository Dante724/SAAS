// New job / New estimate — one screen, no wizard. Everything is optional, so a walk-in is one tap.
// Customer search (or "Add 'Maria 0412…'" inline) → their devices as chips → problem → photos →
// who's on it. Creating is online only with an Idempotency-Key, so a retried tap can't make two jobs;
// photos upload after the job exists and never block it.
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Camera, Check, ChevronLeft, Loader2, Plus, Search, Smartphone, User, UserPlus, X } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card, Page, PrimaryButton } from "../components/ui";
import { addPhotosToJob, newKey, shrinkPhoto, uploadPhoto } from "../lib/photos";
import { addProblem, orderBody, splitNamePhone, type NewDevice } from "../lib/newJob";
import { inferRole, savedRole } from "../lib/role";
import { announceChange, useStaff, useToast } from "../lib/useGo";

type Device = { id: string; brand?: string | null; model?: string | null; serial?: string | null; imei?: string | null };
type Client = { id: string; name: string; phone?: string | null; devices?: Device[] };

const PROBLEMS = ["Cracked screen", "Battery", "Charging port", "Water damage", "Won't turn on", "Software"];
const deviceLabel = (d: Device) => [d.brand, d.model].filter(Boolean).join(" ") || d.serial || d.imei || "Device";
const field = "w-full min-h-[3rem] rounded-2xl border border-slate-200 bg-white px-4 text-[1rem] text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15";
const chip = (on: boolean) => `min-h-[2.75rem] px-4 rounded-full border text-[0.9375rem] font-medium inline-flex items-center gap-1.5 ${on ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`;

function Label({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 px-1 text-[0.8125rem] font-semibold text-ink-tertiary">{children}</p>;
}

export default function NewJob() {
  const nav = useNavigate();
  const loc = useLocation();
  const toast = useToast();
  const { me, can } = useMe();
  const staff = useStaff();
  const mayEstimate = can("can_create_estimates");
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
  // problem, photos, who
  const [complaint, setComplaint] = useState("");
  const [photos, setPhotos] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const role = savedRole() ?? inferRole(me);
  const [who, setWho] = useState<string>("");
  useEffect(() => { if (me?.id && role === "tech") setWho(me.id); }, [me?.id, role]);
  const mayAssign = can("can_assign_staff");
  // create
  const [busy, setBusy] = useState<string | null>(null);
  const jobKey = useRef(newKey());

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

  async function create() {
    if (busy) return;
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Creating a job needs a connection so it gets its number.", tone: "warn" }); return; }
    setBusy("Creating…");
    try {
      const body = orderBody({ isEstimate, clientId: client?.id, deviceId, newDevice, complaint, assignedToId: who === "me" ? me?.id : who || null });
      const o = await api.postOnce<{ id: string; order_number: string }>("/orders", body, jobKey.current);
      if (photos.length) {
        setBusy(`Uploading photos 0/${photos.length}`);
        const ids: string[] = [];
        for (const [i, p] of photos.entries()) {
          try { ids.push(await uploadPhoto(p)); } catch { /* one bad upload doesn't lose the job */ }
          setBusy(`Uploading photos ${i + 1}/${photos.length}`);
        }
        if (ids.length) await addPhotosToJob(o.id, ids).catch(() => {});
        if (ids.length < photos.length) toast({ title: `${photos.length - ids.length} photo(s) didn't upload`, body: "Add them again from the job.", tone: "warn" });
      }
      announceChange("jobs");
      toast({ title: `${isEstimate ? "Estimate" : "Job"} ${o.order_number} created` });
      nav(`/job/${encodeURIComponent(o.id)}`, { replace: true });
    } catch (e: any) {
      toast({ title: `Couldn't create the ${isEstimate ? "estimate" : "job"}`, body: e?.message, tone: "error" });
      setBusy(null);
    }
  }

  const back = (
    <button onClick={() => (history.length > 1 ? nav(-1) : nav("/today"))} className="-ml-2 h-11 min-w-[2.75rem] pr-2 inline-flex items-center text-primary font-medium">
      <ChevronLeft size={26} />Cancel
    </button>
  );
  const term = q.trim();
  const split = splitNamePhone(term);

  return (
    <Page title={isEstimate ? "New estimate" : "New job"} back={back}>
      {mayEstimate && (
        <div className="mb-5 grid grid-cols-2 rounded-2xl bg-slate-100 p-1" role="tablist">
          {[false, true].map((est) => (
            <button key={String(est)} role="tab" aria-selected={isEstimate === est} onClick={() => setIsEstimate(est)}
              className={`min-h-[2.75rem] rounded-xl text-[0.9375rem] font-semibold ${isEstimate === est ? "bg-white text-ink-primary shadow-clay-sm" : "text-ink-secondary"}`}>
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
            <div className="min-w-0 flex-1"><p className="font-semibold text-ink-primary truncate">{client.name}</p>{client.phone && <p className="text-[0.875rem] text-ink-secondary">{client.phone}</p>}</div>
            <button onClick={() => { setClient(null); setDeviceId(null); setNewDevice(null); }} aria-label="Change customer" className="h-11 w-11 inline-flex items-center justify-center rounded-full text-ink-tertiary active:bg-slate-100"><X size={20} /></button>
          </Card>
        ) : adding ? (
          <Card className="p-3 space-y-2">
            <input autoFocus value={adding.name} onChange={(e) => setAdding({ ...adding, name: e.target.value })} placeholder="Name" className={field} />
            <input value={adding.phone} onChange={(e) => setAdding({ ...adding, phone: e.target.value })} placeholder="Phone" inputMode="tel" className={field} />
            <div className="flex gap-2">
              <button onClick={() => setAdding(null)} className="min-h-[2.75rem] flex-1 rounded-2xl border border-slate-200 font-semibold text-ink-primary">Back</button>
              <button onClick={saveClient} disabled={!adding.name.trim() || savingClient} className="min-h-[2.75rem] flex-1 rounded-2xl bg-primary font-semibold text-white disabled:opacity-50">{savingClient ? "Adding…" : "Add customer"}</button>
            </div>
          </Card>
        ) : (
          <>
            <div className="relative">
              <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or phone" autoComplete="off" enterKeyHint="search" className={`${field} pl-11`} />
              {searching && <Loader2 size={18} className="absolute right-4 top-1/2 -translate-y-1/2 animate-spin text-ink-tertiary" />}
            </div>
            {(results.length > 0 || term.length >= 2) && (
              <Card className="mt-2 overflow-hidden divide-y divide-slate-100">
                {results.map((c) => (
                  <button key={c.id} onClick={() => pickClient(c)} className="w-full min-h-[3.25rem] px-4 py-2 text-left active:bg-slate-50">
                    <p className="font-medium text-ink-primary">{c.name}</p>
                    <p className="text-[0.8125rem] text-ink-secondary truncate">{[c.phone, ...(c.devices || []).slice(0, 2).map(deviceLabel)].filter(Boolean).join(" · ")}</p>
                  </button>
                ))}
                {term.length >= 2 && (
                  <button onClick={() => setAdding({ name: split.name, phone: split.phone })} className="w-full min-h-[3.25rem] px-4 flex items-center gap-2 text-left font-semibold text-primary active:bg-slate-50">
                    <UserPlus size={18} />Add “{term}”
                  </button>
                )}
              </Card>
            )}
            <p className="mt-2 px-1 text-[0.8125rem] text-ink-tertiary">Leave empty for a walk-in.</p>
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
            <div className="mt-2 grid grid-cols-2 gap-2">
              <input value={newDevice.brand} onChange={(e) => setNewDevice({ ...newDevice, brand: e.target.value })} placeholder="Brand" className={field} />
              <input value={newDevice.model} onChange={(e) => setNewDevice({ ...newDevice, model: e.target.value })} placeholder="Model" className={field} />
              <input value={newDevice.imei} onChange={(e) => setNewDevice({ ...newDevice, imei: e.target.value })} placeholder="IMEI" inputMode="numeric" className={`${field} col-span-2`} />
              <input value={newDevice.serial} onChange={(e) => setNewDevice({ ...newDevice, serial: e.target.value })} placeholder="Serial number" autoCapitalize="characters" className={`${field} col-span-2`} />
            </div>
          )}
        </section>
      )}

      <section className="mb-5">
        <Label>Problem</Label>
        <textarea value={complaint} onChange={(e) => setComplaint(e.target.value)} rows={3} placeholder="What's wrong?"
          className={`${field} py-3 min-h-[5.5rem] resize-none`} />
        <div className="mt-2 flex flex-wrap gap-2">
          {PROBLEMS.map((p) => <button key={p} onClick={() => setComplaint((c) => addProblem(c, p))} className={chip(false)}>{p}</button>)}
        </div>
      </section>

      <section className="mb-5">
        <Label>Photos</Label>
        <div className="flex gap-2 overflow-x-auto pb-1">
          <button onClick={() => fileRef.current?.click()} className="h-20 w-20 shrink-0 rounded-2xl border-2 border-dashed border-slate-300 inline-flex flex-col items-center justify-center gap-1 text-ink-secondary active:bg-slate-50">
            <Camera size={22} /><span className="text-[0.75rem] font-medium">Add</span>
          </button>
          {photos.map((p, i) => (
            <div key={i} className="relative h-20 w-20 shrink-0">
              <img src={p} alt={`Photo ${i + 1}`} className="h-20 w-20 rounded-2xl object-cover" />
              <button onClick={() => setPhotos((l) => l.filter((_, j) => j !== i))} aria-label="Remove photo"
                className="absolute -right-1.5 -top-1.5 h-7 w-7 rounded-full bg-ink-primary text-white inline-flex items-center justify-center"><X size={14} /></button>
            </div>
          ))}
        </div>
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

      <div className="sticky bottom-[calc(env(safe-area-inset-bottom)+5.75rem)] md:bottom-[calc(env(safe-area-inset-bottom)+5rem)] z-10">
        <PrimaryButton onClick={create} disabled={!!busy}>{busy || (isEstimate ? "Create estimate" : "Create job")}</PrimaryButton>
      </div>
    </Page>
  );
}
