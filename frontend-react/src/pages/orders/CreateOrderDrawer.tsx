import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import ScanButton from "../../components/ScanButton";
import { api } from "../../api/client";
import { readAsDataUrl } from "../../lib/files";
import { alertDialog } from "../../lib/dialog";
import AuthedImage from "../../components/AuthedImage";
import PhotoAnnotator from "./PhotoAnnotator";
import { money } from "./types";
import NewClientDrawer from "../../components/NewClientDrawer";
import CustomFields from "../../components/CustomFields";
import CatalogPicker from "../../components/CatalogPicker";
import { useCurrencies, type CurItem } from "../../hooks/useCurrencies";
import { useTerminology } from "../../lib/useTerminology";
import { identifierFields } from "../../lib/assetIdentifiers";
import IdentifyHint, { type IdMatch } from "../../components/IdentifyHint";
import NameSuggestInput from "../../components/NameSuggestInput";

type ClientLite = { id: string; name?: string; phone?: string; email?: string };
type Emp = { id: string; name: string };
type LineDraft = { id: string; description: string; kind: string; price: number };
type WOType = { id: string; name: string; color?: string | null };

// Fallback chips when the org has no configured WO types (no type_id sent then).
const TYPES: { t: string; dot: string }[] = [
  { t: "Repair", dot: "bg-white" },
  { t: "Diagnostics", dot: "bg-purple-500" },
  { t: "Warranty", dot: "bg-emerald-500" },
  { t: "Maintenance", dot: "bg-amber-500" },
];

export default function CreateOrderDrawer({ isEstimate: initEstimate, prefillClient, onClose, onCreated }: { isEstimate?: boolean; prefillClient?: ClientLite | null; onClose: () => void; onCreated: () => void }) {
  const term = useTerminology();
  const [estimate, setEstimate] = useState(!!initEstimate);
  const [step, setStep] = useState<1 | 2>(1);
  const [type, setType] = useState("Repair");
  const [woTypes, setWoTypes] = useState<WOType[]>([]);
  const [woCustom, setWoCustom] = useState<Record<string, any>>({});
  // Real configured types drive the chips when present; else fall back to static labels.
  const typeId = woTypes.find((t) => t.name === type)?.id || null;
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ClientLite[]>([]);
  const [client, setClient] = useState<ClientLite | null>(prefillClient || null);
  const [priority, setPriority] = useState(0);
  const [managerId, setManagerId] = useState(""); const [assigneeId, setAssigneeId] = useState("");
  const [scheduled, setScheduled] = useState(""); const [duration, setDuration] = useState(60);
  const [complaint, setComplaint] = useState("");
  const [group, setGroup] = useState("Device"); const [brand, setBrand] = useState(""); const [model, setModel] = useState(""); const [serial, setSerial] = useState("");
  // 144ka: optional deeper asset identifiers, revealed by the "+ More details" expander (mirrors the WO/asset drawer).
  const [imei, setImei] = useState(""); const [vin, setVin] = useState(""); const [hin, setHin] = useState("");
  const [color, setColor] = useState(""); const [condition, setCondition] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  // An existing device picked from the "Seen before" card — the order attaches to it instead of
  // creating a duplicate asset.
  const [deviceId, setDeviceId] = useState<string | null>(null);
  function pickExisting(m: IdMatch) {
    setDeviceId(m.id);
    if (m.brand) setBrand(m.brand);
    if (m.model) setModel(m.model);
    if (m.color) setColor(m.color);
    if (!client && m.client) setClient({ id: m.client.id, name: m.client.name, phone: m.client.phone });
  }
  const [assetCustom, setAssetCustom] = useState<Record<string, any>>({});
  const [lines, setLines] = useState<LineDraft[]>([]);
  const [emps, setEmps] = useState<Emp[]>([]);
  const [newClient, setNewClient] = useState(false);
  const [busy, setBusy] = useState(false);
  // Intake condition photos — uploaded to /api/media at pick time; ids ride along on create.
  const [intakePhotos, setIntakePhotos] = useState<{ id: string; url: string }[]>([]);
  const [annoPhoto, setAnnoPhoto] = useState<{ id: string; url: string } | null>(null);
  const [upBusy, setUpBusy] = useState(false);
  async function saveMarkup(origId: string, dataUrl: string) {
    try {
      // 144dg — keep the original intake shot; add the marked copy alongside (no order yet to link to).
      const r = await api.post<any>("/media", { data: dataUrl, name: "markup.jpg", mime: "image/jpeg" });
      setIntakePhotos((cur) => {
        const i = cur.findIndex((p) => p.id === origId);
        if (i < 0) return [...cur, { id: r.id, url: r.url }];
        return [...cur.slice(0, i + 1), { id: r.id, url: r.url }, ...cur.slice(i + 1)];
      });
      setAnnoPhoto(null);
    } catch (e: any) { setErr(e.message || "Could not save markup"); }
  }
  async function addIntakePhotos(files: FileList | null) {
    if (!files?.length) return;
    setUpBusy(true);
    try {
      const added: { id: string; url: string }[] = [];
      for (const f of Array.from(files)) {
        const data = await readAsDataUrl(f);
        const r = await api.post<any>("/media", { data, name: f.name, mime: f.type });
        added.push({ id: r.id, url: r.url });
      }
      setIntakePhotos((cur) => [...cur, ...added]);
    } catch (e: any) { setErr(e.message || "Upload failed (file may be too large)"); }
    finally { setUpBusy(false); }
  }
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(() => {}); }, []);
  useEffect(() => { api.get<any>("/forms/types").then((r) => { const it: WOType[] = r.items || []; setWoTypes(it); if (it.length) setType((cur) => it.some((t) => t.name === cur) ? cur : it[0].name); }).catch(() => {}); }, []);
  useEffect(() => {
    if (client || q.trim().length < 2) { setResults([]); return; }
    const h = setTimeout(() => { api.get<any>(`/clients?limit=8&q=${encodeURIComponent(q.trim())}`).then((r) => setResults(r.items || [])).catch(() => setResults([])); }, 250);
    return () => clearTimeout(h);
  }, [q, client]);

  const subtotal = lines.reduce((s, l) => s + (l.price || 0), 0);
  // Taxes/discounts are applied server-side from the org's tax rules when the order is created;
  // don't fabricate a flat tax here (this is a pre-tax items estimate).
  const total = subtotal;

  // Document display currency (optional override; blank = client preference / base).
  const { base, items: curItems, opts: curOpts, multi: multiCurrency } = useCurrencies();
  const [curCode, setCurCode] = useState("");
  const docCur: CurItem | undefined = curItems.find((c) => c.code === curCode);
  // registry rate is "1 <code> = rate <base>", so a base amount shows in the currency as amount / rate.
  const conv = (amt: number) => (docCur && docCur.rate ? amt / docCur.rate : amt);
  const showConv = !!(docCur && base && curCode !== base.code);

  function addCustomLine() {
    setLines((ls) => [...ls, { id: Math.random().toString(36).slice(2), description: "Custom line", kind: "service", price: 0 }]);
  }

  async function submit(reopen: boolean) {
    setBusy(true); setErr(null);
    try {
      const created = await api.post<any>("/orders", {
        client_id: client?.id || null,
        type_id: typeId,
        custom_data: woCustom,
        complaint: complaint || null,
        priority,
        assigned_to_id: assigneeId || null,
        manager_id: managerId || null,
        scheduled_for: scheduled || null,
        device_id: deviceId,
        asset_brand: brand || null, asset_model: model || null, asset_serial: serial || null,
        asset_imei: imei || null, asset_vin: vin || null, asset_hin: hin || null,
        asset_color: color || null, asset_condition: condition || null,
        asset_custom_data: assetCustom,
        intake_photos: intakePhotos.map((p) => p.id),
        is_estimate: estimate,
        currency_code: curCode || null,
        lines: lines.map((l) => ({ description: l.description, kind: l.kind, qty: 1, unit_price: l.price })),
      });
      onCreated();
      if (!reopen) onClose();
      else { setClient(null); setQ(""); setComplaint(""); setBrand(""); setModel(""); setSerial(""); setImei(""); setVin(""); setHin(""); setColor(""); setCondition(""); setMoreOpen(false); setDeviceId(null); setLines([]); setWoCustom({}); setAssetCustom({}); setStep(1); }
      return created;
    } catch (e: any) { setErr(e.message || "Failed to create"); } finally { setBusy(false); }
  }

  const lbl = "block text-[11px] font-bold uppercase tracking-wider text-ink-tertiary";
  const inp = "glass-input block w-full py-2.5 px-3.5 rounded-xl text-sm font-semibold text-ink-primary";

  // Identifier fields are template-driven: a phone shows IMEI/Serial, a vehicle VIN,
  // a vessel HIN — never a fixed set. Primary shows up top; the rest live under "+ More details".
  const idState: Record<string, [string, (v: string) => void]> = { serial: [serial, setSerial], imei: [imei, setImei], vin: [vin, setVin], hin: [hin, setHin] };
  // The selected group can narrow identifiers further than the template (e.g. Jet Ski → HIN).
  const idRule = term.groups.find((g) => g.label === group)?.identifier || term.identifier;
  const idFields = identifierFields(idRule?.allowed, idRule?.primary);
  const primaryId = idFields[0];
  const moreIds = idFields.slice(1);

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 45 }}>
      <div className="absolute inset-0 bg-ink-primary/30 backdrop-blur-[2.5px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[780px] bg-white/95 backdrop-blur-2xl shadow-clay-drawer flex flex-col border-l border-white/80 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>

        {/* Header */}
        <header className="px-7 pt-5 pb-4 flex items-center justify-between border-b border-slate-100/90 bg-white/70">
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-bold tracking-tight text-ink-primary">New {estimate ? "estimate" : "work order"}</h2>
            <button type="button" onClick={() => setEstimate((v) => !v)} className="text-xs px-2.5 py-1 rounded-full font-medium bg-slate-100 hover:bg-slate-200/90 text-ink-secondary transition-colors border border-slate-200/70 flex items-center gap-1.5 shadow-2xs">
              <span className="w-1.5 h-1.5 rounded-full bg-primary" /><span>Switch to {estimate ? "Work Order" : "Estimate"}</span>
            </button>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100 transition-all"><M n="close" c="text-lg" /></button>
        </header>

        {/* Stepper */}
        <nav className="px-7 py-3 border-b border-slate-100/80 bg-slate-50/60 flex items-center justify-between text-xs">
          <div className="flex items-center space-x-3 w-full">
            <button type="button" onClick={() => setStep(1)} className="flex items-center space-x-2 cursor-pointer">
              <span className={`w-5 h-5 rounded-full font-bold flex items-center justify-center text-[11px] transition-all ${step === 1 ? "bg-primary text-white shadow-sm shadow-primary/30" : "bg-slate-200 text-ink-secondary"}`}>1</span>
              <span className={`font-bold transition-colors ${step === 1 ? "text-primary" : "text-ink-tertiary"}`}>General info</span>
            </button>
            <div className={`flex-1 h-[2px] mx-2 transition-colors ${step === 2 ? "bg-primary" : "bg-slate-200"}`} />
            <button type="button" onClick={() => setStep(2)} className="flex items-center space-x-2 cursor-pointer">
              <span className={`w-5 h-5 rounded-full font-bold flex items-center justify-center text-[11px] transition-all ${step === 2 ? "bg-primary text-white shadow-sm shadow-primary/30" : "bg-slate-200 text-ink-secondary"}`}>2</span>
              <span className={`transition-colors ${step === 2 ? "font-bold text-primary" : "font-medium text-ink-tertiary hover:text-ink-primary"}`}>Services &amp; products</span>
            </button>
          </div>
        </nav>

        {/* Body */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden p-7 space-y-6">
          {step === 1 && (
            <section className="space-y-5">
              <div className="space-y-1.5">
                <label className={lbl}>Work order type</label>
                <div className="flex flex-wrap items-center gap-2">
                  {(woTypes.length ? woTypes.map((t) => ({ t: t.name, dot: "", color: t.color })) : TYPES.map((x) => ({ ...x, color: null }))).map((tp) => (
                    <button key={tp.t} type="button" onClick={() => setType(tp.t)}
                      className={`px-3.5 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1.5 transition-all ${type === tp.t ? "bg-primary text-white shadow-clay-sm ring-2 ring-primary ring-offset-1" : "bg-white border border-slate-200/90 text-ink-secondary hover:bg-slate-50"}`}>
                      <span className={`w-2 h-2 rounded-full ${type === tp.t ? "bg-white" : tp.dot}`} style={!("dot" in tp && tp.dot) && tp.color ? { background: tp.color } : undefined} /><span>{tp.t}</span>
                    </button>
                  ))}
                </div>
              </div>

              {typeId && <CustomFields typeId={typeId} value={woCustom} onChange={setWoCustom} creating inputSize="sm" />}

              <div className="space-y-1.5">
                <label className={lbl}>Client</label>
                {client ? (
                  <div className="glass-input flex items-center gap-2 pl-10 pr-3 py-2.5 rounded-xl relative">
                    <M n="person" c="absolute left-3.5 text-base text-ink-tertiary" />
                    <span className="font-semibold text-sm text-ink-primary">{client.name}</span><span className="text-xs text-ink-tertiary font-mono">{client.phone}</span>
                    <button className="ml-auto text-xs text-primary font-semibold" onClick={() => { setClient(null); setQ(""); }}>Change</button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <div className="relative flex-1">
                      <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-ink-tertiary"><M n="search" c="text-base" /></div>
                      <input value={q} onChange={(e) => setQ(e.target.value)} className="glass-input block w-full pl-10 pr-4 py-2.5 rounded-xl text-sm font-semibold text-ink-primary" placeholder="Search client by name, phone or email… (blank = walk-in)" />
                      {(results.length > 0 || q.trim().length >= 2) && (
                        <div className="absolute z-10 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5">
                          {results.map((r) => <button key={r.id} onClick={() => setClient(r)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm"><span className="font-semibold text-ink-primary">{r.name}</span><span className="text-ink-tertiary font-mono text-xs">{r.phone}</span></button>)}
                          <button onClick={() => setNewClient(true)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm text-primary font-bold border-t border-slate-100 mt-1"><M n="person_add" c="text-base" />Create new client{q.trim() ? ` "${q.trim()}"` : ""}</button>
                        </div>
                      )}
                    </div>
                    <button aria-label="New client" type="button" onClick={() => setNewClient(true)} title="New client" className="w-11 h-11 flex-shrink-0 rounded-xl bg-blue-50 text-primary border border-blue-100 flex items-center justify-center hover:bg-blue-100"><M n="person_add" c="text-lg" /></button>
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5"><label className={lbl}>Priority</label>
                  <div className="relative"><select value={priority} onChange={(e) => setPriority(Number(e.target.value))} className={`${inp} pr-8 appearance-none`}><option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option></select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div>
                </div>
                <div className="space-y-1.5"><label className={lbl}>Manager</label>
                  <div className="relative"><select value={managerId} onChange={(e) => setManagerId(e.target.value)} className={`${inp} pr-8 appearance-none`}><option value="">— Select —</option>{emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div>
                </div>
              </div>

              <div className="space-y-1.5"><label className={lbl}>Specialist (assignee)</label>
                <div className="relative"><select value={assigneeId} onChange={(e) => setAssigneeId(e.target.value)} className={`${inp} pr-8 appearance-none`}><option value="">— Unassigned —</option>{emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5"><label className={lbl}>Scheduled for</label>
                  <DateField mode="datetime" value={scheduled} onChange={setScheduled} />
                </div>
                <div className="space-y-1.5"><label className={lbl}>Duration (minutes)</label>
                  <input type="number" min={15} step={15} value={duration} onChange={(e) => setDuration(Number(e.target.value))} className={inp} /></div>
              </div>

              <div className="space-y-1.5"><label className={lbl}>Complaint / description</label>
                <textarea value={complaint} onChange={(e) => setComplaint(e.target.value)} rows={3} className={`${inp} resize-none`} placeholder="What's the issue?" /></div>

              <div className="pt-3 border-t border-slate-200/80 space-y-3.5">
                <div className="flex items-center gap-2"><span className="text-xs font-bold uppercase tracking-wider text-ink-primary">{term.asset}</span><span className="text-xs text-ink-tertiary font-normal">— auto-added to the client's warehouse</span></div>
                <div className="grid grid-cols-1 sm:grid-cols-12 gap-4 items-start">
                  <div className="sm:col-span-4 space-y-2">
                    <label className="bg-slate-50/80 border-2 border-dashed border-slate-200/90 rounded-2xl p-4 flex flex-col items-center justify-center text-center group hover:border-primary/60 transition-colors cursor-pointer min-h-[140px]">
                      <input type="file" accept="image/*" multiple className="hidden" onChange={(e) => { addIntakePhotos(e.target.files); e.currentTarget.value = ""; }} />
                      <div className="w-10 h-10 rounded-full bg-blue-50 text-primary flex items-center justify-center mb-1.5 group-hover:scale-105 transition-transform"><M n="photo_camera" c="text-xl" /></div>
                      <p className="text-xs font-bold text-ink-primary">{upBusy ? "Uploading…" : <>Condition photos <span className="text-primary underline">browse</span></>}</p>
                      <p className="text-[10px] text-ink-tertiary mt-0.5">How it arrived · JPG / PNG · up to 5 MB</p>
                    </label>
                    {intakePhotos.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {intakePhotos.map((p) => (
                          <span key={p.id} className="relative group">
                            <AuthedImage src={p.url} alt="intake" onClick={() => setAnnoPhoto(p)} className="w-12 h-12 rounded-lg object-cover border border-slate-200 bg-slate-100 cursor-pointer" />
                            <button type="button" onClick={() => setAnnoPhoto(p)} className="absolute bottom-0.5 left-0.5 w-4 h-4 rounded bg-ink-primary/85 text-white flex items-center justify-center opacity-0 group-hover:opacity-100" title="Mark up"><M n="edit" c="text-[10px]" /></button>
                            <button type="button" onClick={() => setIntakePhotos((cur) => cur.filter((x) => x.id !== p.id))} className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-ink-primary text-white text-[10px] leading-none opacity-0 group-hover:opacity-100" title="Remove">×</button>
                          </span>
                        ))}
                      </div>
                    )}
                    {annoPhoto && <PhotoAnnotator src={annoPhoto.url} onSave={(d) => saveMarkup(annoPhoto.id, d)} onClose={() => setAnnoPhoto(null)} />}
                  </div>
                  <div className="sm:col-span-8 space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><label className="block text-[10px] font-bold uppercase text-ink-tertiary">Group</label>
                        <div className="relative"><select value={group} onChange={(e) => setGroup(e.target.value)} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg appearance-none">{(term.groups.length ? term.groups.map((g) => g.label) : ["Device", "Vehicle", "Equipment"]).map((g) => <option key={g}>{g}</option>)}</select><M n="expand_more" c="absolute right-2 top-2 text-ink-tertiary pointer-events-none text-xs" /></div></div>
                      <div className="space-y-1"><label className="block text-[10px] font-bold uppercase text-ink-tertiary">Brand</label><NameSuggestInput field="brand" value={brand} onChange={setBrand} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg" placeholder="Samsung" /></div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><label className="block text-[10px] font-bold uppercase text-ink-tertiary">Model</label><NameSuggestInput field="model" brand={brand} value={model} onChange={setModel} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg" placeholder="Galaxy S24 Ultra" /></div>
                      <div className="space-y-1"><label className="block text-[10px] font-bold uppercase text-ink-tertiary">{primaryId.label}</label>
                        <div className="flex items-center space-x-1.5"><input value={idState[primaryId.kind][0]} onChange={(e) => { idState[primaryId.kind][1](e.target.value); setDeviceId(null); }} className="glass-input font-mono block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg" placeholder={primaryId.placeholder} /><ScanButton title={`Scan ${primaryId.label}`} onScan={(code) => { idState[primaryId.kind][1](code); setDeviceId(null); }} /></div></div>
                    </div>
                    <IdentifyHint value={idState[primaryId.kind][0]} kind={primaryId.kind} clientId={client?.id} usingId={deviceId}
                      onUse={pickExisting} onUndo={() => setDeviceId(null)} />
                  </div>
                </div>

                {!moreOpen ? (
                  <button type="button" onClick={() => setMoreOpen(true)} className="flex items-center gap-1 text-[11px] font-bold text-primary hover:text-primary/80 transition-colors">
                    <M n="add" c="text-sm" />More details
                  </button>
                ) : (
                  <div className="rounded-2xl bg-slate-50/70 border border-slate-200/80 p-3.5 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary">More {term.asset.toLowerCase()} details</span>
                      <button type="button" onClick={() => setMoreOpen(false)} className="text-[11px] font-semibold text-ink-tertiary hover:text-ink-primary">Hide</button>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><label className="block text-[10px] font-bold uppercase text-ink-tertiary">Condition</label>
                        <div className="relative"><select value={condition} onChange={(e) => setCondition(e.target.value)} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg appearance-none"><option value="">—</option>{["New", "Good", "Fair", "Poor", "For parts"].map((c) => <option key={c} value={c}>{c}</option>)}</select><M n="expand_more" c="absolute right-2 top-2 text-ink-tertiary pointer-events-none text-xs" /></div></div>
                      <div className="space-y-1"><label className="block text-[10px] font-bold uppercase text-ink-tertiary">Color</label><input value={color} onChange={(e) => setColor(e.target.value)} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg" placeholder="Titanium Black" /></div>
                    </div>
                    {moreIds.length > 0 && (
                      <div className="grid grid-cols-2 gap-3">
                        {moreIds.map((f) => (
                          <div key={f.kind} className="space-y-1"><label className="block text-[10px] font-bold uppercase text-ink-tertiary">{f.label}</label><input value={idState[f.kind][0]} onChange={(e) => idState[f.kind][1](e.target.value)} className="glass-input font-mono block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg" placeholder={f.placeholder} /></div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <CustomFields entity="device" value={assetCustom} onChange={setAssetCustom} creating inputSize="sm" />
              {err && <div className="text-status-overdue-ink text-xs font-semibold">{err}</div>}
            </section>
          )}

          {step === 2 && (
            <section className="space-y-5">
              <p className="text-xs text-ink-tertiary leading-relaxed">Add the services and products for this order. You can also create it now and add these later.</p>
              <div className="flex items-center space-x-1 border-b border-slate-200/80 pb-2">
                <button type="button" className="px-3.5 py-1 text-xs font-bold rounded-lg bg-blue-50 text-primary">All</button>
                <button type="button" className="px-3.5 py-1 text-xs font-semibold text-ink-secondary hover:text-ink-primary">Services</button>
                <button type="button" className="px-3.5 py-1 text-xs font-semibold text-ink-secondary hover:text-ink-primary">Products</button>
              </div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-bold uppercase tracking-wider text-ink-tertiary">Line items</span>
                <button type="button" onClick={async () => {
                  if (!complaint.trim()) { await alertDialog("Add a complaint/description first."); return; }
                  try {
                    const r = await api.post<any>("/ai/estimate/draft", { complaint, device: [brand, model].filter(Boolean).join(" ") });
                    const add = (r.lines || []).map((l: any) => ({ id: Math.random().toString(36).slice(2), description: l.description, kind: "service", price: l.unit_price || 0 }));
                    if (add.length) setLines((ls) => [...ls, ...add]);
                  } catch (e: any) { await alertDialog(e.message || "AI draft failed"); }
                }} className="px-2.5 py-1 rounded-lg bg-indigo-600 text-white text-[11px] font-bold shadow-clay-sm flex items-center gap-1"><M n="auto_awesome" c="text-xs" />AI draft from complaint</button>
              </div>
              <CatalogPicker placeholder="Search a service or product to add…" onPick={(i) => setLines((ls) => [...ls, { id: Math.random().toString(36).slice(2), description: i.name, kind: i.kind, price: i.price || 0 }])} />

              <div className="space-y-2.5">
                <div className="flex items-center justify-between text-[11px] font-bold uppercase tracking-wider text-ink-tertiary px-1"><span>Configured items</span><span>Subtotal</span></div>
                {lines.length === 0 && <p className="text-xs text-ink-tertiary px-1">No items yet — add a custom line below or create and add later.</p>}
                {lines.map((l) => (
                  <div key={l.id} className="flex items-center justify-between p-3.5 rounded-xl bg-white border border-slate-200 shadow-sm hover:border-primary/40 transition-all">
                    <div className="flex items-center space-x-3">
                      <div className="w-8 h-8 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center"><M n="build" c="text-base" /></div>
                      <div className="flex-1"><input value={l.description} onChange={(e) => setLines((ls) => ls.map((x) => x.id === l.id ? { ...x, description: e.target.value } : x))} className="text-sm font-bold text-ink-primary bg-transparent focus:outline-none w-full" /></div>
                    </div>
                    <div className="flex items-center space-x-3">
                      <input type="number" value={l.price} onChange={(e) => setLines((ls) => ls.map((x) => x.id === l.id ? { ...x, price: Number(e.target.value) } : x))} className="glass-input w-24 py-1 px-2 text-sm font-mono font-bold text-ink-primary rounded-lg text-right" />
                      <button aria-label="Delete" type="button" onClick={() => setLines((ls) => ls.filter((x) => x.id !== l.id))} className="text-ink-tertiary hover:text-rose-600 p-1 transition-colors"><M n="delete" c="text-base" /></button>
                    </div>
                  </div>
                ))}
                <button type="button" onClick={addCustomLine} className="w-full flex items-center space-x-3 p-3 rounded-xl border border-dashed border-primary/40 bg-blue-50/40 hover:bg-blue-50 transition-colors text-left group">
                  <div className="w-7 h-7 rounded-full bg-primary text-white flex items-center justify-center shadow-xs group-hover:scale-105 transition-transform"><M n="add" c="text-sm font-bold" /></div>
                  <div><p className="text-xs font-bold text-primary">Add a custom line</p><p className="text-[11px] text-ink-tertiary">Free-text, set your own custom price</p></div>
                </button>
              </div>

              <div className="bg-slate-50/90 rounded-2xl p-4 border border-slate-200 space-y-2">
                {multiCurrency && (
                  <div className="flex items-center justify-between pb-2 mb-1 border-b border-slate-200">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-ink-tertiary">Document currency</span>
                    <select value={curCode} onChange={(e) => setCurCode(e.target.value)} className="glass-input py-1 px-2 text-xs font-semibold text-ink-primary rounded-lg">
                      <option value="">Client / base default</option>{curOpts.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                    </select>
                  </div>
                )}
                <div className="flex justify-between text-xs text-ink-secondary"><span>Subtotal</span><span className="font-mono font-semibold text-ink-primary">{money(subtotal)}</span></div>
                <div className="pt-2 border-t border-slate-200 flex justify-between items-baseline"><span className="text-xs font-bold text-ink-primary">Items subtotal <span className="font-normal text-ink-tertiary">(before tax)</span></span><span className="text-base font-extrabold font-mono text-primary">{money(total)}</span></div>
                {showConv && docCur && (
                  <div className="pt-2 border-t border-dashed border-slate-200 flex justify-between items-baseline">
                    <span className="text-[11px] text-ink-tertiary">≈ in {docCur.code} <span className="font-mono">(1 {docCur.code} = {docCur.rate} {base?.code})</span></span>
                    <span className="text-sm font-bold font-mono text-ink-secondary">{docCur.symbol}{conv(total).toFixed(2)}</span>
                  </div>
                )}
              </div>
              {err && <div className="text-status-overdue-ink text-xs font-semibold">{err}</div>}
            </section>
          )}
        </div>

        {/* Footer */}
        <footer className="px-7 py-4 border-t border-slate-100 bg-white/95 backdrop-blur-md flex items-center justify-between">
          {step === 1 ? (
            <div className="w-full flex items-center justify-between">
              <button type="button" onClick={onClose} className="px-4 py-2 text-xs font-bold text-ink-secondary hover:text-ink-primary transition-colors">Cancel</button>
              <button type="button" onClick={() => setStep(2)} className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5 transition-all"><span>Next: Services &amp; products</span><M n="arrow_forward" c="text-xs" /></button>
            </div>
          ) : (
            <div className="w-full flex items-center justify-between">
              <button type="button" onClick={() => setStep(1)} className="px-4 py-2 rounded-xl text-xs font-bold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50 transition-colors flex items-center gap-1"><M n="arrow_back" c="text-xs" /><span>Back</span></button>
              <div className="flex items-center space-x-2">
                <button type="button" disabled={busy} onClick={() => submit(true)} className="hidden sm:inline-flex px-3.5 py-2 rounded-xl text-xs font-bold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50 transition-colors">Save &amp; create another</button>
                <button type="button" disabled={busy} onClick={() => submit(false)} className="px-5 py-2 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110 transition-all">{busy ? "Creating…" : `Create ${estimate ? "estimate" : "work order"}`}</button>
              </div>
            </div>
          )}
        </footer>
      </div>
      {newClient && <NewClientDrawer prefillName={q.trim()} onClose={() => setNewClient(false)} onCreated={(c) => { setClient(c); setNewClient(false); setQ(""); setResults([]); }} />}
    </div>
  );
}
