// The inquiry form body, shared by "New inquiry" and the inquiry drawer: contact, channel,
// type, the device (brand / model / serial or IMEI), what they asked for, and the shop's own
// custom fields (Settings → Forms → Inquiry).
import CustomFields from "../../components/CustomFields";
import { CHANNELS, type Lead, type LeadType } from "./inquiries";

const lbl = "block font-semibold text-ink-secondary mb-1";
const fld = "glass-input w-full rounded-xl px-3 py-2 text-sm";

export default function InquiryFields({ value, onChange, types, creating, showSla }: {
  value: Lead & { sla_minutes?: number }; onChange: (v: Lead & { sla_minutes?: number }) => void;
  types: LeadType[]; creating: boolean; showSla?: boolean;
}) {
  const set = (k: string, v: any) => onChange({ ...value, [k]: v });
  const active = types.filter((t) => t.active || t.id === value.lead_type_id);
  const picked = types.find((t) => t.id === value.lead_type_id);
  return (
    <div className="space-y-4 text-xs">
      <div><label htmlFor="inq-name" className={lbl}>Name <span className="text-rose-500">*</span></label>
        <input id="inq-name" autoFocus={creating} value={value.name || ""} onChange={(e) => set("name", e.target.value)} className={`${fld} font-semibold`} /></div>
      <div className="grid grid-cols-2 gap-3">
        <div><label htmlFor="inq-phone" className={lbl}>Phone</label><input id="inq-phone" value={value.phone || ""} onChange={(e) => set("phone", e.target.value)} className={`${fld} tabular-nums`} /></div>
        <div><label htmlFor="inq-email" className={lbl}>Email</label><input id="inq-email" type="email" value={value.email || ""} onChange={(e) => set("email", e.target.value)} className={fld} /></div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div><label htmlFor="inq-type" className={lbl}>Type</label>
          <select id="inq-type" value={value.lead_type_id || ""} onChange={(e) => set("lead_type_id", e.target.value || null)} className={`${fld} font-semibold appearance-none`}>
            <option value="">No type</option>
            {active.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select></div>
        <div><label htmlFor="inq-channel" className={lbl}>Channel</label>
          <select id="inq-channel" value={value.source || "phone"} onChange={(e) => set("source", e.target.value)} className={`${fld} font-semibold appearance-none`}>
            {CHANNELS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></div>
      </div>
      {showSla && (picked
        ? <p className="text-[11px] text-ink-tertiary">Respond within {picked.sla_minutes >= 120 ? `${Math.round(picked.sla_minutes / 60)} hours` : `${picked.sla_minutes} minutes`} (set by the {picked.name} type).</p>
        : <div className="w-1/2 pr-1.5"><label htmlFor="inq-sla" className={lbl}>Respond within (minutes)</label>
            <input id="inq-sla" type="number" min={1} value={value.sla_minutes ?? 60} onChange={(e) => set("sla_minutes", e.target.value)} className={`${fld} font-mono`} /></div>)}
      <fieldset className="p-3 rounded-xl bg-slate-50/90 border border-slate-200/80 space-y-3">
        <legend className="px-1 text-[11px] font-bold text-ink-primary">Device</legend>
        <div className="grid grid-cols-2 gap-3">
          <div><label htmlFor="inq-brand" className={lbl}>Brand</label><input id="inq-brand" value={value.device_brand || ""} onChange={(e) => set("device_brand", e.target.value)} placeholder="Apple" className={fld} /></div>
          <div><label htmlFor="inq-model" className={lbl}>Model</label><input id="inq-model" value={value.device_model || ""} onChange={(e) => set("device_model", e.target.value)} placeholder="iPhone 14" className={fld} /></div>
        </div>
        <div><label htmlFor="inq-serial" className={lbl}>Serial number or IMEI</label><input id="inq-serial" value={value.serial || ""} onChange={(e) => set("serial", e.target.value)} className={`${fld} font-mono`} /></div>
      </fieldset>
      <div><label htmlFor="inq-text" className={lbl}>What they asked for</label>
        <textarea id="inq-text" value={value.inquiry || ""} onChange={(e) => set("inquiry", e.target.value)} className={`${fld} h-24 resize-none`} placeholder="What is the customer asking about?" /></div>
      <CustomFields entity="lead" value={value.custom_data || {}} onChange={(v) => set("custom_data", v)} creating={creating} />
    </div>
  );
}
