// Label templates: which product field {Barcode} encodes, and the barcode type for each field
// (Orderry parity A4). EAN-13 / UPC-A are checked at print time; a product whose value isn't a
// valid code is named in the error instead of printing an unscannable label.

export const BARCODE_FIELDS = [
  { key: "barcode", label: "Barcode" },
  { key: "sku", label: "SKU" },
  { key: "code", label: "Code" },
  { key: "mpn", label: "MPN" },
] as const;
export const BARCODE_TYPES = [
  { key: "code128", label: "Code 128", hint: "Any letters and digits." },
  { key: "ean13", label: "EAN-13", hint: "12 or 13 digits; the check digit is added or verified." },
  { key: "upca", label: "UPC-A", hint: "11 or 12 digits; the check digit is added or verified." },
  { key: "qr", label: "QR code", hint: "Any text, scans with a phone camera." },
] as const;
export const LABEL_DOC_TYPES = ["label", "price_tag", "product_label"];

export type BarcodeOptions = { barcode_field: string | null; barcode_types: Record<string, string> };

/** The type used for a field: its saved choice, else Code 128. */
export const typeFor = (o: BarcodeOptions, field: string) => o.barcode_types?.[field] || "code128";

export default function LabelBarcodePicker({ value, onChange }: { value: BarcodeOptions; onChange: (v: BarcodeOptions) => void }) {
  const field = value.barcode_field || "";
  const kind = field ? typeFor(value, field) : "code128";
  const hint = BARCODE_TYPES.find((t) => t.key === kind)?.hint;
  const sel = "w-full bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5";
  return (
    <div className="space-y-2 pt-1 border-t border-slate-100">
      <div>
        <label htmlFor="lbl-bc-field" className="block text-[11px] font-semibold text-ink-tertiary mb-1">{"{Barcode}"} encodes</label>
        <select id="lbl-bc-field" value={field} className={sel}
          onChange={(e) => onChange({ ...value, barcode_field: e.target.value || null })}>
          <option value="">First filled: barcode, SKU, code</option>
          {BARCODE_FIELDS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
      </div>
      {field && (
        <div>
          <label htmlFor="lbl-bc-type" className="block text-[11px] font-semibold text-ink-tertiary mb-1">Barcode type for {BARCODE_FIELDS.find((f) => f.key === field)?.label}</label>
          <select id="lbl-bc-type" value={kind} className={sel}
            onChange={(e) => onChange({ ...value, barcode_types: { ...(value.barcode_types || {}), [field]: e.target.value } })}>
            {BARCODE_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
          {hint && <p className="mt-1 text-[11px] text-ink-tertiary">{hint}</p>}
        </div>
      )}
    </div>
  );
}
