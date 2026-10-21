// Service drawer fields (Catalog › Services). Every field the API stores is editable here, and
// serviceBody() sends all of them back, so saving a service never drops warranty, barcode,
// booking visibility or booking rules (Orderry parity A1/A2).
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { humanize } from "../../lib/humanize";
import { currencySymbol } from "./types";
import CategoryPicker from "../../components/CategoryPicker";
import { inputCls } from "../invoices/billing";

const lbl = "block font-semibold text-ink-secondary mb-1";
const field = `${inputCls} text-ink-primary`;
const box = "p-3 rounded-xl bg-slate-50 border border-slate-200";
// Resource.kind values; a shop's own resources can add more, and older services may carry free text.
const BASE_KINDS = ["bay", "room", "equipment", "other"];

type Emp = { id: string; name?: string; active?: boolean };

/** The full PUT/POST body for a service: every stored field, so nothing is reset on save. */
export function serviceBody(p: any) {
  return {
    name: (p.name || "").trim(), description: p.description || "",
    category_id: p.category_id || null, category: p.category || null,
    is_labor: !!p.is_labor, unit: (p.unit || "pc").trim() || "pc",
    price: Number(p.price) || 0, cost_price: Number(p.cost_price) || 0,
    duration_min: Math.max(0, Number(p.duration_min) || 0), flat_rate: !!p.flat_rate,
    warranty_days: Math.max(0, Number(p.warranty_days) || 0),
    barcode: (p.barcode || "").trim() || null,
    bookable: p.bookable !== false,
    allowed_employee_ids: Array.isArray(p.allowed_employee_ids) ? p.allowed_employee_ids : [],
    required_resource_types: Array.isArray(p.required_resource_types) ? p.required_resource_types : [],
  };
}

function Chip({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick}
      className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-colors ${on ? "bg-primary/10 text-primary border-primary/30" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>
      {label}
    </button>
  );
}

export default function ServiceForm({ p, set }: { p: any; set: (k: string, v: any) => void }) {
  const [emps, setEmps] = useState<Emp[] | null>(null);
  const [empErr, setEmpErr] = useState(false);
  const [kinds, setKinds] = useState<string[]>(BASE_KINDS);
  const [newKind, setNewKind] = useState("");
  useEffect(() => {
    api.get<any>("/staff/employees")
      .then((r) => { const d = r?.items ?? r; setEmps((Array.isArray(d) ? d : []).filter((e: Emp) => e.active !== false)); })
      .catch(() => { setEmps([]); setEmpErr(true); });
    api.get<any>("/resources")
      .then((r) => { const own = (r?.items || []).map((x: any) => x.kind).filter(Boolean); setKinds((k) => Array.from(new Set([...k, ...own]))); })
      .catch(() => { /* the four standard kinds still work */ });
  }, []);

  const allowed: string[] = Array.isArray(p.allowed_employee_ids) ? p.allowed_employee_ids : [];
  const needs: string[] = Array.isArray(p.required_resource_types) ? p.required_resource_types : [];
  const toggle = (k: string, list: string[], v: string) => set(k, list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  // one chip per kind, case-insensitively; the service's own spelling wins over a resource's
  const kindList = Array.from(new Map([...kinds, ...needs].map((k) => [k.toLowerCase(), k])).values());
  // an allowed employee who has since left still shows, so the rule isn't silently kept hidden
  const missing = allowed.filter((id) => emps && !emps.some((e) => e.id === id));
  const addKind = () => {
    const k = newKind.trim();
    if (k && !needs.some((x) => x.toLowerCase() === k.toLowerCase())) set("required_resource_types", [...needs, k]);
    setNewKind("");
  };
  // enum-style keys ("bay", "equipment") read as words; anything a person typed shows as typed
  const kindLabel = (k: string) => (/^[a-z_]+$/.test(k) ? humanize(k) : k);

  return (
    <div className="space-y-3.5">
      <div><label htmlFor="svc-name" className={lbl}>Service title <span className="text-rose-500">*</span></label><input id="svc-name" value={p.name || ""} onChange={(e) => set("name", e.target.value)} className={field} /></div>
      <div><label htmlFor="svc-desc" className={lbl}>Description</label><textarea id="svc-desc" value={p.description || ""} onChange={(e) => set("description", e.target.value)} className={`${field} h-20 resize-none`} /></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div><label className={lbl}>Category</label><CategoryPicker entity="service" value={p.category_id} onChange={(id) => set("category_id", id)} /></div>
        <div><label htmlFor="svc-dur" className={lbl}>Standard duration (min)</label><input id="svc-dur" type="number" min={0} value={p.duration_min ?? 60} onChange={(e) => set("duration_min", e.target.value)} className={`${field} tabular-nums`} /></div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div><label htmlFor="svc-price" className={lbl}>Labor price ({currencySymbol()})</label><input id="svc-price" type="number" value={p.price ?? 0} onChange={(e) => set("price", e.target.value)} className={`${field} tabular-nums font-semibold`} /></div>
        <div><label htmlFor="svc-cost" className={lbl}>Cost ({currencySymbol()})</label><input id="svc-cost" type="number" value={p.cost_price ?? 0} onChange={(e) => set("cost_price", e.target.value)} className={`${field} tabular-nums`} /></div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div><label htmlFor="svc-unit" className={lbl}>Unit</label><input id="svc-unit" value={p.unit ?? "pc"} onChange={(e) => set("unit", e.target.value)} maxLength={24} placeholder="pc" className={field} /></div>
        <div><label htmlFor="svc-warranty" className={lbl}>Warranty (days)</label><input id="svc-warranty" type="number" min={0} value={p.warranty_days ?? 0} onChange={(e) => set("warranty_days", e.target.value)} className={`${field} tabular-nums`} /></div>
        <div><label htmlFor="svc-barcode" className={lbl}>Barcode</label><input id="svc-barcode" value={p.barcode || ""} onChange={(e) => set("barcode", e.target.value)} className={`${field} font-mono`} /></div>
      </div>
      <label className={`flex items-center justify-between cursor-pointer ${box}`}><span className="text-ink-secondary font-medium">This is a labor operation</span><input type="checkbox" checked={!!p.is_labor} onChange={(e) => set("is_labor", e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300" /></label>
      {/* 144ax G3 — flat-rate labour: bill the standard duration × the fee as an hourly rate */}
      <label className={`flex items-center justify-between cursor-pointer ${box}`}><span className="text-ink-secondary font-medium">Flat-rate <span className="block text-xs text-ink-tertiary font-normal">Bills booked hours × the price, so the price is the hourly rate.</span></span><input type="checkbox" checked={!!p.flat_rate} onChange={(e) => set("flat_rate", e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300" /></label>

      <div className="p-4 rounded-xl bg-white border border-slate-200 space-y-3.5">
        <div className="text-sm font-semibold text-ink-primary">Online booking</div>
        <label className="flex items-center justify-between gap-3 cursor-pointer">
          <span className="text-ink-secondary font-medium">Show on online booking page<span className="block text-xs text-ink-tertiary font-normal">Clients can pick this service when they book online.</span></span>
          <input type="checkbox" role="switch" aria-label="Show on online booking page" checked={p.bookable !== false} onChange={(e) => set("bookable", e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300 shrink-0" />
        </label>
        <div>
          <span className={lbl}>Who can perform it</span>
          <p className="text-xs text-ink-tertiary mb-1.5">None selected means anyone. Online bookings only offer these people.</p>
          {emps === null ? <p className="text-xs text-ink-tertiary">Loading team…</p>
            : empErr ? <p className="text-xs text-rose-600">Couldn't load the team. Close and reopen to try again.</p>
            : emps.length === 0 && missing.length === 0 ? <p className="text-xs text-ink-tertiary">No team members yet.</p>
            : <div className="flex flex-wrap gap-1.5">
                {emps.map((e) => <Chip key={e.id} on={allowed.includes(e.id)} label={e.name || "Unnamed"} onClick={() => toggle("allowed_employee_ids", allowed, e.id)} />)}
                {missing.map((id) => <Chip key={id} on label="Former team member" onClick={() => toggle("allowed_employee_ids", allowed, id)} />)}
              </div>}
        </div>
        <div>
          <span className={lbl}>Resources it needs</span>
          <div className="flex flex-wrap gap-1.5 items-center">
            {kindList.map((k) => <Chip key={k} on={needs.includes(k)} label={kindLabel(k)} onClick={() => toggle("required_resource_types", needs, k)} />)}
            <input value={newKind} onChange={(e) => setNewKind(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addKind(); } }}
              onBlur={addKind} placeholder="Add another…" aria-label="Add a resource type" maxLength={40} className="px-2.5 py-1 rounded-full text-xs w-32 bg-white border border-dashed border-slate-300 text-ink-primary outline-none focus:border-primary focus:border-solid focus:ring-2 focus:ring-primary/10" />
          </div>
        </div>
      </div>
    </div>
  );
}
