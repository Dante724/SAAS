import { useEffect, useState, type ReactElement } from "react";
import { sectionCard } from "../../components/Section";
import { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import ScanButton from "../../components/ScanButton";
import { api } from "../../api/client";
import { autoPrintForJob } from "../../lib/autoPrint";
import AutoPrintPicker from "../../components/AutoPrintPicker";
import { useClientSearch } from "../../hooks/useClientSearch";
import { readAsDataUrl } from "../../lib/files";
import { alertDialog } from "../../lib/dialog";
import AuthedImage from "../../components/AuthedImage";
import PhotoAnnotator from "./PhotoAnnotator";
import { money } from "./types";
import NewClientDrawer from "../../components/NewClientDrawer";
import CustomFields, { isVisible } from "../../components/CustomFields";
import CatalogPicker from "../../components/CatalogPicker";
import { useCurrencies, type CurItem } from "../../hooks/useCurrencies";
import { useTerminology } from "../../lib/useTerminology";
import { identifierFields } from "../../lib/assetIdentifiers";
import { useMe } from "../../context/Me";
import IdentifyHint, { type IdMatch } from "../../components/IdentifyHint";
import NameSuggestInput from "../../components/NameSuggestInput";
import { JobSuggestions } from "../../components/sense/Sense";

type ClientLite = { id: string; name?: string; phone?: string; email?: string };
type Emp = { id: string; name: string; in_managers?: boolean; in_assignees?: boolean };
type LineDraft = { id: string; description: string; kind: string; price: number; ref_id?: string };
type WOType = { id: string; name: string; color?: string | null };

// Fallback chips when the org has no configured WO types (no type_id sent then).
const TYPES: { t: string; dot: string }[] = [
  { t: "Repair", dot: "bg-slate-400" },
  { t: "Diagnostics", dot: "bg-slate-400" },
  { t: "Warranty", dot: "bg-slate-400" },
  { t: "Maintenance", dot: "bg-slate-400" },
];

export default function CreateOrderDrawer({ isEstimate: initEstimate, prefillClient, onClose, onCreated }: { isEstimate?: boolean; prefillClient?: ClientLite | null; onClose: () => void; onCreated: () => void }) {
  const term = useTerminology();
  const { can } = useMe();
  const canCustomize = can("can_manage_settings");
  const estimate = !!initEstimate;   // set by the button that opened it: New order or New estimate
  const [step, setStep] = useState<1 | 2>(1);
  const [type, setType] = useState("Repair");
  const [woTypes, setWoTypes] = useState<WOType[]>([]);
  const [woCustom, setWoCustom] = useState<Record<string, any>>({});
  // What the customer says is wrong: the complaint box, or (when a shop's form asks for it in
  // its own text fields, e.g. "Fault reported") those fields. Drafting and suggestions read this.
  const issueText = () => complaint.trim() || Object.values(woCustom).filter((v) => typeof v === "string" && v.trim()).join(". ");
  // Real configured types drive the chips when present; else fall back to static labels.
  const typeId = woTypes.find((t) => t.name === type)?.id || null;
  useEffect(() => {
    if (!typeId) { setLayout(null); return; }
    api.get<{ items: any[] }>(`/forms/layout?form=order&type_id=${typeId}`).then((r) => setLayout(r.items || null)).catch(() => setLayout(null));
  }, [typeId]);
  const [q, setQ] = useState("");
  const [cOpen, setCOpen] = useState(false);   // client dropdown open (opens on tap, lists newest first)
  const [client, setClient] = useState<ClientLite | null>(prefillClient || null);
  const [priority, setPriority] = useState(0);
  const [managerId, setManagerId] = useState(""); const [assigneeId, setAssigneeId] = useState("");
  const [scheduled, setScheduled] = useState(""); const [duration, setDuration] = useState(60);
  const [pickType, setPickType] = useState<"all" | "service" | "product">("all");   // step 2 search filter
  const [complaint, setComplaint] = useState("");
  const [group, setGroup] = useState("Device"); const [brand, setBrand] = useState(""); const [model, setModel] = useState(""); const [serial, setSerial] = useState("");
  // 144ka: optional deeper asset identifiers, revealed by the "+ More details" expander (mirrors the WO/asset drawer).
  const [imei, setImei] = useState(""); const [vin, setVin] = useState(""); const [hin, setHin] = useState("");
  const [color, setColor] = useState(""); const [condition, setCondition] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  // An existing device picked from the "Seen before" card — the order attaches to it instead of
  // creating a duplicate asset.
  const [deviceId, setDeviceId] = useState<string | null>(null);
  // Field order / visibility / required for this work-order type (Settings → Forms → Customize).
  type LayoutItem = { ref: string; kind: string; key: string; label: string; visible: boolean; required: boolean; require?: "" | "intake" | "close"; field?: any };
  const [layout, setLayout] = useState<LayoutItem[] | null>(null);
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
  const results = useClientSearch<ClientLite>(q, cOpen && !client);

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

  // A drawer line as the API wants it: products are parts, everything else labour; catalog picks
  // keep their ref_id so stock, cost and the catalog price stay linked.
  const lineBody = (l: LineDraft) => ({
    description: l.description, kind: l.kind, line_type: l.kind === "product" ? "part" : "labour",
    ref_id: l.ref_id || null, qty: 1, unit_price: l.price,
  });

  function addCustomLine() {
    setLines((ls) => [...ls, { id: Math.random().toString(36).slice(2), description: "Custom line", kind: "service", price: 0 }]);
  }

  async function submit(reopen: boolean) {
    const m = missingRequired(); if (m) { setErr(m); setStep(1); return; }
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
        duration_min: duration || 60,
        device_id: deviceId,
        asset_brand: brand || null, asset_model: model || null, asset_serial: serial || null,
        asset_imei: imei || null, asset_vin: vin || null, asset_hin: hin || null,
        asset_color: color || null, asset_condition: condition || null,
        asset_custom_data: assetCustom,
        intake_photos: intakePhotos.map((p) => p.id),
        is_estimate: estimate,
        currency_code: curCode || null,
        // Bundles expand into their components only through the add-line endpoint (below).
        lines: lines.filter((l) => l.kind !== "bundle").map(lineBody),
      });
      for (const b of lines.filter((l) => l.kind === "bundle")) await api.post(`/orders/${created.id}/lines`, lineBody(b));
      if (!estimate) void autoPrintForJob(created.id);   // label templates set to print on new jobs
      onCreated();
      if (!reopen) onClose();
      else { setClient(null); setQ(""); setComplaint(""); setBrand(""); setModel(""); setSerial(""); setImei(""); setVin(""); setHin(""); setColor(""); setCondition(""); setMoreOpen(false); setDeviceId(null); setLines([]); setWoCustom({}); setAssetCustom({}); setStep(1); }
      return created;
    } catch (e: any) { setErr(e.message || "Couldn't create it. Check the fields and try again."); } finally { setBusy(false); }
  }

  const lbl = "block text-xs font-medium text-ink-secondary";
  const inp = "glass-input block w-full h-10 px-3.5 rounded-xl text-sm font-medium text-ink-primary";

  // Identifier fields are template-driven: a phone shows IMEI/Serial, a vehicle VIN,
  // a vessel HIN — never a fixed set. Primary shows up top; the rest live under "+ More details".
  const idState: Record<string, [string, (v: string) => void]> = { serial: [serial, setSerial], imei: [imei, setImei], vin: [vin, setVin], hin: [hin, setHin] };
  // The selected group can narrow identifiers further than the template (e.g. Jet Ski → HIN).
  const idRule = term.groups.find((g) => g.label === group)?.identifier || term.identifier;
  const idFields = identifierFields(idRule?.allowed, idRule?.primary);
  const primaryId = idFields[0];
  const moreIds = idFields.slice(1);

  const reqKeys = new Set((layout || []).filter((i) => i.kind === "system" && i.visible && i.required).map((i) => i.key));
  const closeKeys = new Set((layout || []).filter((i) => i.kind === "system" && i.visible && i.require === "close").map((i) => i.key));
  const star = (k: string) => (reqKeys.has(k) ? <span className="text-rose-500"> *</span>
    : closeKeys.has(k) ? <span className="ml-1.5 px-1.5 py-px rounded bg-amber-50 text-[11px] font-semibold normal-case tracking-normal text-amber-700">Needed to close</span> : null);
  function missingRequired(): string | null {
    const filled: Record<string, boolean> = {
      client: !!client, complaint: !!complaint.trim(), manager: !!managerId, assignee: !!assigneeId,
      scheduled_for: !!scheduled, priority: true,
      asset: !!(deviceId || brand || model || serial || imei || vin || hin),
    };
    const empty = (v: any) => v === undefined || v === null || (Array.isArray(v) ? !v.length : String(v).trim() === "");
    const miss = (layout || []).filter((i) => i.visible && i.required && (i.kind === "system" ? !filled[i.key]
      : i.kind === "custom" && i.field && i.field.field_type !== "bool" && isVisible(i.field, woCustom) && empty(woCustom[i.key])));
    return miss.length ? `Fill in ${miss.map((i) => i.label).join(", ")}` : null;
  }
  function next() { const m = missingRequired(); if (m) { setErr(m); return; } setErr(null); setStep(2); }

  // Built-in parts of the form, placed by the shop's layout (Settings → Forms → Customize).
  const sysBlocks: Record<string, ReactElement> = {
    client: (
<div className="space-y-1.5">
                <label className={lbl}>Customer{star("client")}</label>
                {client ? (
                  <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{(client.name || "?").split(/\s+/).map((w: string) => w[0]).slice(0, 2).join("").toUpperCase()}</span>
                    <span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold text-ink-primary">{client.name}</span><span className="block text-xs text-ink-tertiary tabular-nums">{client.phone || "No phone"}</span></span>
                    <button type="button" className="h-8 rounded-lg px-2.5 text-xs font-semibold text-primary hover:bg-white" onClick={() => { setClient(null); setQ(""); }}>Change</button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <div className="relative flex-1">
                      <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-ink-tertiary"><M n="search" c="text-base" /></div>
                      <input value={q} onChange={(e) => { setQ(e.target.value); setCOpen(true); }} onFocus={() => setCOpen(true)} onClick={() => setCOpen(true)}
                        onKeyDown={(e) => { if (e.key === "Escape") setCOpen(false); }} role="combobox" aria-expanded={cOpen} autoComplete="off"
                        className="glass-input block w-full h-10 pl-10 pr-4 rounded-xl text-sm font-medium text-ink-primary" placeholder="Search by name, phone or email (leave empty for a walk-in)" />
                      {cOpen && (<>
                        <div className="fixed inset-0 z-[9]" onClick={() => setCOpen(false)} />
                        <div className="absolute z-10 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 max-h-72 overflow-y-auto">
                          {!q.trim() && results.length > 0 && <div className="px-2.5 pt-1 pb-0.5 text-[11px] text-ink-tertiary">Recent customers</div>}
                          {q.trim() && results.length === 0 && <div className="px-2.5 py-2 text-xs text-ink-tertiary">No customer matches "{q.trim()}"</div>}
                          {results.map((r) => <button key={r.id} onClick={() => { setClient(r); setCOpen(false); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm"><span className="font-semibold text-ink-primary">{r.name}</span><span className="text-ink-tertiary font-mono text-xs">{r.phone}</span></button>)}
                          <button onClick={() => { setNewClient(true); setCOpen(false); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm text-primary font-bold border-t border-slate-100 mt-1"><M n="person_add" c="text-base" />New customer{q.trim() ? ` "${q.trim()}"` : ""}</button>
                        </div>
                      </>)}
                    </div>
                    <button aria-label="New customer" type="button" onClick={() => setNewClient(true)} title="New customer" className="h-10 flex-shrink-0 rounded-xl border border-slate-200 bg-white px-3 text-xs font-semibold text-ink-primary shadow-[0_1px_2px_rgba(15,23,42,0.04)] hover:bg-slate-50 flex items-center gap-1.5"><M n="person_add" c="text-base text-ink-tertiary" />New customer</button>
                  </div>
                )}
              </div>
    ),
    priority: (
<div className="space-y-1.5"><label className={lbl}>Priority{star("priority")}</label>
                  <div className="relative"><select value={priority} onChange={(e) => setPriority(Number(e.target.value))} className={`${inp} pr-8 appearance-none`}><option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option></select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div>
                </div>
    ),
    manager: (
<div className="space-y-1.5"><label className={lbl}>Manager{star("manager")}</label>
                  <div className="relative"><select value={managerId} onChange={(e) => setManagerId(e.target.value)} className={`${inp} pr-8 appearance-none`}><option value="">Choose a manager</option>{emps.filter((e) => e.in_managers !== false).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div>
                </div>
    ),
    assignee: (
<div className="space-y-1.5"><label className={lbl}>Technician{star("assignee")}</label>
                <div className="relative"><select value={assigneeId} onChange={(e) => setAssigneeId(e.target.value)} className={`${inp} pr-8 appearance-none`}><option value="">Unassigned</option>{emps.filter((e) => e.in_assignees !== false).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div>
              </div>
    ),
    scheduled_for: (
<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5"><label className={lbl}>Scheduled for{star("scheduled_for")}</label>
                  <DateField mode="datetime" value={scheduled} onChange={setScheduled} />
                </div>
                <div className="space-y-1.5"><label className={lbl}>Duration (minutes)</label>
                  <input type="number" min={15} step={15} value={duration} onChange={(e) => setDuration(Number(e.target.value))} className={inp} /></div>
              </div>
    ),
    complaint: (
<><div className="space-y-1.5"><label className={lbl}>What the customer reported{star("complaint")}</label>
                <textarea value={complaint} onChange={(e) => setComplaint(e.target.value)} rows={3} className={`${inp} h-auto py-2.5 resize-none leading-relaxed`} placeholder="Screen cracked after a drop, touch still works" /></div></>
    ),
    asset: (
<><div className="space-y-3.5">
                <p className="-mt-2 text-xs text-ink-tertiary">Saved to the customer's {term.asset.toLowerCase()}s{star("asset")}</p>
                <div className="grid grid-cols-1 sm:grid-cols-12 gap-4 items-start">
                  <div className="sm:col-span-4 space-y-2">
                    <label className="bg-slate-50/60 border border-dashed border-slate-300 rounded-2xl p-4 flex flex-col items-center justify-center text-center group hover:border-primary/50 hover:bg-white transition-colors cursor-pointer min-h-[132px]">
                      <input type="file" accept="image/*" multiple className="hidden" onChange={(e) => { addIntakePhotos(e.target.files); e.currentTarget.value = ""; }} />
                      <div className="w-10 h-10 rounded-full bg-white text-ink-secondary ring-1 ring-slate-200 shadow-sm flex items-center justify-center mb-2 group-hover:text-primary transition-colors"><M n="photo_camera" c="text-lg" /></div>
                      <p className="text-xs font-semibold text-ink-primary">{upBusy ? "Uploading…" : "Condition photos"}</p>
                      <p className="text-[11px] text-ink-tertiary mt-0.5">How it arrived · drop or <span className="text-primary">browse</span></p>
                    </label>
                    {intakePhotos.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {intakePhotos.map((p) => (
                          <span key={p.id} className="relative group">
                            <AuthedImage src={p.url} alt="intake" onClick={() => setAnnoPhoto(p)} className="w-12 h-12 rounded-lg object-cover border border-slate-200 bg-slate-100 cursor-pointer" />
                            <button type="button" onClick={() => setAnnoPhoto(p)} className="absolute bottom-0.5 left-0.5 w-4 h-4 rounded bg-ink-primary/85 text-white flex items-center justify-center opacity-0 group-hover:opacity-100" title="Mark up"><M n="edit" c="text-[11px]" /></button>
                            <button type="button" onClick={() => setIntakePhotos((cur) => cur.filter((x) => x.id !== p.id))} className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-ink-primary text-white text-[11px] leading-none opacity-0 group-hover:opacity-100" title="Remove">×</button>
                          </span>
                        ))}
                      </div>
                    )}
                    {annoPhoto && <PhotoAnnotator src={annoPhoto.url} onSave={(d) => saveMarkup(annoPhoto.id, d)} onClose={() => setAnnoPhoto(null)} />}
                  </div>
                  <div className="sm:col-span-8 space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><label className="block text-xs font-medium text-ink-secondary">Group</label>
                        <div className="relative"><select value={group} onChange={(e) => setGroup(e.target.value)} className="glass-input block w-full h-10 px-3 text-sm font-medium text-ink-primary rounded-xl appearance-none">{(term.groups.length ? term.groups.map((g) => g.label) : ["Device", "Vehicle", "Equipment"]).map((g) => <option key={g}>{g}</option>)}</select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div></div>
                      <div className="space-y-1"><label className="block text-xs font-medium text-ink-secondary">Brand</label><NameSuggestInput field="brand" group={group} template={term.key} value={brand} onChange={setBrand} className="glass-input block w-full h-10 px-3 text-sm font-medium text-ink-primary rounded-xl" placeholder="Samsung" /></div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><label className="block text-xs font-medium text-ink-secondary">Model</label><NameSuggestInput field="model" brand={brand} group={group} template={term.key} value={model} onChange={setModel} className="glass-input block w-full h-10 px-3 text-sm font-medium text-ink-primary rounded-xl" placeholder="Galaxy S24 Ultra" /></div>
                      <div className="space-y-1"><label className="block text-xs font-medium text-ink-secondary">{primaryId.label}</label>
                        <div className="flex items-center space-x-1.5"><input value={idState[primaryId.kind][0]} onChange={(e) => { idState[primaryId.kind][1](e.target.value); setDeviceId(null); }} className="glass-input font-mono block w-full h-10 px-3 text-sm font-medium text-ink-primary rounded-xl" placeholder={primaryId.placeholder} /><ScanButton title={`Scan ${primaryId.label}`} onScan={(code) => { idState[primaryId.kind][1](code); setDeviceId(null); }} /></div></div>
                    </div>
                    <IdentifyHint value={idState[primaryId.kind][0]} kind={primaryId.kind} clientId={client?.id} usingId={deviceId}
                      onUse={pickExisting} onUndo={() => setDeviceId(null)} />
                  </div>
                </div>

                {!moreOpen ? (
                  <button type="button" onClick={() => setMoreOpen(true)} className="flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary/80 transition-colors">
                    <M n="add" c="text-sm" />More details
                  </button>
                ) : (
                  <div className="rounded-xl bg-slate-50/70 border border-slate-200/80 p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold text-ink-tertiary">More {term.asset.toLowerCase()} details</span>
                      <button type="button" onClick={() => setMoreOpen(false)} className="text-[11px] font-semibold text-ink-tertiary hover:text-ink-primary">Hide</button>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><label className="block text-xs font-medium text-ink-secondary">Condition</label>
                        <div className="relative"><select value={condition} onChange={(e) => setCondition(e.target.value)} className="glass-input block w-full h-10 px-3 text-sm font-medium text-ink-primary rounded-xl appearance-none"><option value="">—</option>{["New", "Good", "Fair", "Poor", "For parts"].map((c) => <option key={c} value={c}>{c}</option>)}</select><M n="expand_more" c="absolute right-3 top-3 text-ink-tertiary pointer-events-none text-sm" /></div></div>
                      <div className="space-y-1"><label className="block text-xs font-medium text-ink-secondary">Color</label><input value={color} onChange={(e) => setColor(e.target.value)} className="glass-input block w-full h-10 px-3 text-sm font-medium text-ink-primary rounded-xl" placeholder="Titanium Black" /></div>
                    </div>
                    {moreIds.length > 0 && (
                      <div className="grid grid-cols-2 gap-3">
                        {moreIds.map((f) => (
                          <div key={f.kind} className="space-y-1"><label className="block text-xs font-medium text-ink-secondary">{f.label}</label><input value={idState[f.kind][0]} onChange={(e) => idState[f.kind][1](e.target.value)} className="glass-input tabular-nums block w-full h-10 px-3 text-sm font-medium text-ink-primary rounded-xl" placeholder={f.placeholder} /></div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <CustomFields entity="device" value={assetCustom} onChange={setAssetCustom} creating inputSize="sm" /></>
    ),
  };

  // Fields in the order set in Customize: built-in blocks and the type's custom fields interleaved.
  const closeIds = (layout || []).filter((i) => i.require === "close" && i.kind === "custom").map((i) => i.ref.slice(3));
  // Each built-in block belongs to a section; neighbours of the same section share one card, and the
  // shop's custom fields join the card they sit in. The shop's order is kept exactly (Customize form).
  const SECTION: Record<string, { title: string; icon: string }> = {
    client: { title: "Customer", icon: "person" },
    asset: { title: term.asset, icon: "smartphone" },
    complaint: { title: "What's wrong", icon: "build" },
    priority: { title: "Team and schedule", icon: "groups" },
    manager: { title: "Team and schedule", icon: "groups" },
    assignee: { title: "Team and schedule", icon: "groups" },
    scheduled_for: { title: "Team and schedule", icon: "groups" },
  };
  const DEFAULT_ORDER = ["client", "priority", "manager", "assignee", "scheduled_for", "complaint", "asset"];
  function renderLayout() {
    type Part = { key: string; el: ReactElement; wide?: boolean };
    const cards: { title: string; icon: string; parts: Part[] }[] = [];
    const put = (sec: { title: string; icon: string } | null, part: Part) => {
      const last = cards[cards.length - 1];
      if (sec && (!last || last.title !== sec.title)) cards.push({ ...sec, parts: [part] });
      else if (last) last.parts.push(part);
      else cards.push({ title: "Details", icon: "tune", parts: [part] });
    };
    if (!layout) {
      if (typeId) put({ title: "Details", icon: "tune" }, { key: "cf", el: <CustomFields typeId={typeId} value={woCustom} onChange={setWoCustom} creating inputSize="sm" />, wide: true });
      for (const k of DEFAULT_ORDER) put(SECTION[k], { key: k, el: sysBlocks[k], wide: k === "scheduled_for" || k === "client" || k === "complaint" || k === "asset" });
    } else {
      let run: string[] = [];
      const flush = () => {
        if (run.length && typeId) put(null, { key: `cf-${run[0]}`, wide: true, el: <CustomFields typeId={typeId} only={run} bare closeIds={closeIds} value={woCustom} onChange={setWoCustom} creating inputSize="sm" /> });
        run = [];
      };
      for (const it of layout) {
        if (!it.visible) continue;
        if (it.kind === "system") { flush(); if (sysBlocks[it.key]) put(SECTION[it.key] || null, { key: it.ref, el: sysBlocks[it.key], wide: it.key === "scheduled_for" || it.key === "client" || it.key === "complaint" || it.key === "asset" }); }
        else run.push(it.ref.slice(3));
      }
      flush();
    }
    return <>{cards.map((c, i) => (
      <section key={i + c.title} className={`${sectionCard} p-5`}>
        <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-ink-primary">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-slate-100 text-ink-secondary"><M n={c.icon} c="text-[15px]" /></span>{c.title}
        </h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {c.parts.map((pt) => <div key={pt.key} className={pt.wide ? "sm:col-span-2" : ""}>{pt.el}</div>)}
        </div>
      </section>
    ))}</>;
  }

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 45 }}>
      <div className="absolute inset-0 bg-ink-primary/30 backdrop-blur-[2.5px]" onClick={onClose} />
      <div className="intake relative h-full w-full max-w-[780px] bg-slate-50 shadow-clay-drawer flex flex-col border-l border-slate-200/80 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>

        {/* Header */}
        <header className="px-7 pt-5 pb-4 flex items-center justify-between bg-white">
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-semibold tracking-tight text-ink-primary">New {estimate ? "estimate" : "work order"}</h2>
          </div>
          <div className="flex items-center gap-1">
            {canCustomize && typeId && (
              <a href={`/settings?section=forms&form=type:${typeId}`} target="_blank" rel="noopener" title="Reorder, add or hide fields on this form"
                className="text-xs font-semibold text-ink-tertiary hover:text-primary flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-slate-100"><M n="tune" c="text-sm" />Customize form</a>
            )}
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100 transition-all"><M n="close" c="text-lg" /></button>
          </div>
        </header>

        {/* Stepper */}
        <nav className="px-7 pb-4 bg-white border-b border-slate-200/80 flex items-center justify-between text-xs">
          <div className="flex items-center space-x-3 w-full">
            <button type="button" onClick={() => setStep(1)} className="flex items-center space-x-2 cursor-pointer">
              <span className={`w-6 h-6 rounded-full font-semibold flex items-center justify-center text-[11px] transition-all ${step === 1 ? "bg-primary text-white" : "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"}`}>{step === 1 ? "1" : <M n="check" c="text-[13px]" />}</span>
              <span className={`font-semibold transition-colors ${step === 1 ? "text-ink-primary" : "text-ink-secondary"}`}>General info</span>
            </button>
            <div className={`flex-1 h-px mx-3 transition-colors ${step === 2 ? "bg-primary/40" : "bg-slate-200"}`} />
            <button type="button" onClick={next} className="flex items-center space-x-2 cursor-pointer">
              <span className={`w-6 h-6 rounded-full font-semibold flex items-center justify-center text-[11px] transition-all ${step === 2 ? "bg-primary text-white" : "bg-white text-ink-tertiary ring-1 ring-slate-200"}`}>2</span>
              <span className={`transition-colors ${step === 2 ? "font-semibold text-ink-primary" : "font-medium text-ink-tertiary hover:text-ink-primary"}`}>Services and products</span>
            </button>
          </div>
        </nav>

        {/* Body */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden px-7 py-6 space-y-4">
          {step === 1 && (
            <section className="space-y-4">
              <div className={`${sectionCard} p-5`}>
                <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink-primary"><span className="grid h-7 w-7 place-items-center rounded-lg bg-slate-100 text-ink-secondary"><M n="label" c="text-[15px]" /></span>{estimate ? "Estimate" : "Work order"} type</h3>
                <div className="flex flex-wrap items-center gap-2">
                  {(woTypes.length ? woTypes.map((t) => ({ t: t.name, dot: "", color: t.color })) : TYPES.map((x) => ({ ...x, color: null }))).map((tp) => (
                    <button key={tp.t} type="button" onClick={() => setType(tp.t)}
                      className={`h-9 px-3.5 rounded-xl text-sm font-medium flex items-center gap-2 border transition-all ${type === tp.t ? "border-primary/40 bg-primary/[0.06] text-primary ring-1 ring-primary/20" : "border-slate-200 bg-white text-ink-secondary hover:border-slate-300 hover:text-ink-primary"}`}>
                      <span className={`w-2 h-2 rounded-full ${tp.dot}`} style={!("dot" in tp && tp.dot) && tp.color ? { background: tp.color } : undefined} /><span>{tp.t}</span>
                    </button>
                  ))}
                </div>
              </div>

              {renderLayout()}
            </section>
          )}

          {step === 2 && (
            <section className={`${sectionCard} p-5 space-y-4`}>
              <div className="flex items-start gap-2">
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-slate-100 text-ink-secondary"><M n="inventory_2" c="text-[15px]" /></span>
                <div><h3 className="text-sm font-semibold text-ink-primary">Services and products</h3>
                  <p className="text-xs text-ink-tertiary">Add them now, or create the {estimate ? "estimate" : "job"} and add them later at the bench.</p></div>
              </div>
              <div role="group" aria-label="Show in search" className="inline-flex items-center gap-0.5 rounded-xl bg-slate-100 p-0.5">
                {([["all", "All"], ["service", "Services"], ["product", "Products"]] as const).map(([k, l]) => (
                  <button key={k} type="button" aria-pressed={pickType === k} onClick={() => setPickType(k)}
                    className={`h-7 px-3 text-xs rounded-[10px] font-medium transition ${pickType === k ? "bg-white text-ink-primary shadow-sm ring-1 ring-slate-200/70" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
                ))}
              </div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-medium text-ink-secondary">Line items</span>
                <button type="button" onClick={async () => {
                  if (!issueText()) { await alertDialog("Fill in what the customer reported first."); return; }
                  try {
                    const r = await api.post<any>("/ai/estimate/draft", { complaint: issueText(), device: [brand, model].filter(Boolean).join(" ") });
                    const add = (r.lines || []).map((l: any) => ({ id: Math.random().toString(36).slice(2), description: l.description, kind: "service", price: l.unit_price || 0 }));
                    if (add.length) setLines((ls) => [...ls, ...add]);
                  } catch (e: any) { await alertDialog(e.message || "Couldn't draft lines from the complaint. Add them from the search instead."); }
                }} className="h-8 rounded-lg px-2.5 text-xs font-semibold text-primary hover:bg-primary/5 flex items-center gap-1"><M n="auto_awesome" c="text-sm" />Draft from the complaint</button>
              </div>
              <JobSuggestions complaint={issueText()} deviceId={deviceId}
                onAdd={(i) => setLines((ls) => [...ls, { id: Math.random().toString(36).slice(2), description: i.description, kind: i.kind, price: i.price }])} />
              <CatalogPicker key={pickType} type={pickType} placeholder={pickType === "service" ? "Search a service to add…" : pickType === "product" ? "Search a product to add…" : "Search a service or product to add…"} onPick={(i) => setLines((ls) => [...ls, { id: Math.random().toString(36).slice(2), description: i.name, kind: i.kind, price: i.price || 0, ref_id: i.ref_id }])} />

              <div className="space-y-2.5">
                {lines.length > 0 && <div className="flex items-center justify-between text-xs font-medium text-ink-tertiary px-1"><span>Added</span><span>Price</span></div>}
                {lines.length === 0 && <p className="rounded-xl border border-dashed border-slate-200 px-4 py-5 text-center text-xs text-ink-tertiary">Nothing added yet. Search above, or add a line of your own.</p>}
                {lines.map((l) => (
                  <div key={l.id} className="flex items-center justify-between gap-3 px-3.5 py-3 rounded-xl bg-white border border-slate-200 hover:border-slate-300 transition-all">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <div className="w-8 h-8 shrink-0 rounded-lg bg-slate-100 text-ink-secondary flex items-center justify-center"><M n={l.kind === "product" ? "inventory_2" : l.kind === "bundle" ? "package_2" : "build"} c="text-base" /></div>
                      <div className="min-w-0 flex-1"><input value={l.description} onChange={(e) => setLines((ls) => ls.map((x) => x.id === l.id ? { ...x, description: e.target.value } : x))} className="text-sm font-medium text-ink-primary bg-transparent focus:outline-none w-full" />
                        <span className="block text-xs text-ink-tertiary">{l.kind === "product" ? "Product" : l.kind === "bundle" ? "Kit" : "Service"}</span></div>
                    </div>
                    <div className="flex items-center space-x-3">
                      <input type="number" value={l.price} onChange={(e) => setLines((ls) => ls.map((x) => x.id === l.id ? { ...x, price: Number(e.target.value) } : x))} className="glass-input w-28 h-9 px-2.5 text-sm font-medium tabular-nums text-ink-primary rounded-xl text-right" />
                      <button aria-label="Delete" type="button" onClick={() => setLines((ls) => ls.filter((x) => x.id !== l.id))} className="text-ink-tertiary hover:text-rose-600 p-1 transition-colors"><M n="delete" c="text-base" /></button>
                    </div>
                  </div>
                ))}
                {can("can_add_custom_service") && <button type="button" onClick={addCustomLine} className="flex h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs font-semibold text-primary hover:bg-primary/5">
                  <M n="add" c="text-sm" />Add a line of your own
                </button>}
              </div>

              <div className="rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200/70 space-y-2">
                {multiCurrency && (
                  <div className="flex items-center justify-between pb-2 mb-1 border-b border-slate-200">
                    <span className="text-xs font-medium text-ink-secondary">Document currency</span>
                    <select value={curCode} onChange={(e) => setCurCode(e.target.value)} className="glass-input py-1 px-2 text-xs font-semibold text-ink-primary rounded-lg">
                      <option value="">Customer default</option>{curOpts.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                    </select>
                  </div>
                )}
                <div className="flex justify-between text-xs text-ink-secondary"><span>Subtotal</span><span className="tabular-nums font-semibold text-ink-primary">{money(subtotal)}</span></div>
                <div className="pt-2 border-t border-slate-200 flex justify-between items-baseline"><span className="text-sm font-semibold text-ink-primary">Total <span className="text-xs font-normal text-ink-tertiary">before tax</span></span><span className="text-xl font-semibold tabular-nums tracking-tight text-ink-primary">{money(total)}</span></div>
                {showConv && docCur && (
                  <div className="pt-2 border-t border-dashed border-slate-200 flex justify-between items-baseline">
                    <span className="text-[11px] text-ink-tertiary">≈ in {docCur.code} <span className="font-mono">(1 {docCur.code} = {docCur.rate} {base?.code})</span></span>
                    <span className="text-sm font-bold tabular-nums text-ink-secondary">{docCur.symbol}{conv(total).toFixed(2)}</span>
                  </div>
                )}
              </div>
            </section>
          )}
        </div>

        {/* Errors sit just above the buttons, where the user is looking, not at the end of a long form. */}
        {err && (
          <div role="alert" className="mx-7 mb-0 mt-2 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs font-semibold text-status-overdue-ink">
            <M n="error" c="text-sm" /><span className="min-w-0 flex-1">{err}</span>
            <button type="button" aria-label="Dismiss" onClick={() => setErr(null)} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-sm" /></button>
          </div>
        )}

        {/* Footer */}
        <footer className="px-7 py-3.5 border-t border-slate-200/80 bg-white flex items-center justify-between">
          {step === 1 ? (
            <div className="w-full flex items-center justify-between">
              <button type="button" onClick={onClose} className="h-10 px-4 rounded-xl text-sm font-medium text-ink-secondary border border-slate-200 bg-white hover:bg-slate-50 transition-colors">Cancel</button>
              <button type="button" onClick={next} className="h-10 px-5 rounded-xl bg-primary text-white text-sm font-semibold shadow-sm hover:brightness-110 flex items-center gap-1.5 transition-all"><span>Next: services and products</span><M n="arrow_forward" c="text-sm" /></button>
            </div>
          ) : (
            <div className="w-full flex items-center justify-between">
              <button type="button" onClick={() => setStep(1)} className="h-10 px-4 rounded-xl text-sm font-medium text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50 transition-colors flex items-center gap-1.5"><M n="arrow_back" c="text-sm" /><span>Back</span></button>
              <div className="flex items-center space-x-2">
                {!estimate && <AutoPrintPicker event="create" />}
                <button type="button" disabled={busy} onClick={() => submit(true)} className="hidden sm:inline-flex h-10 items-center px-4 rounded-xl text-sm font-medium text-ink-primary bg-white border border-slate-200 hover:bg-slate-50 transition-colors">Save and create another</button>
                <button type="button" disabled={busy} onClick={() => submit(false)} className="h-10 px-5 rounded-xl bg-primary text-white text-sm font-semibold shadow-sm hover:brightness-110 transition-all">{busy ? "Creating…" : `Create ${estimate ? "estimate" : "work order"}`}</button>
              </div>
            </div>
          )}
        </footer>
      </div>
      {newClient && <NewClientDrawer prefillName={q.trim()} onClose={() => setNewClient(false)} onCreated={(c) => { setClient(c); setNewClient(false); setQ(""); }} />}
    </div>
  );
}
