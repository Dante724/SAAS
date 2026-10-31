// WS-C #12 — a contact's address as separate fields (street, city, region, postcode, country).
// The server rebuilds the single address line from these, so documents and exports that print
// one line keep working. A contact saved before the split shows its old line until the fields
// are filled in.
export type Address = { addr_street?: string | null; addr_city?: string | null; addr_region?: string | null; addr_postcode?: string | null; addr_country?: string | null };
export const ADDRESS_KEYS: (keyof Address)[] = ["addr_street", "addr_city", "addr_region", "addr_postcode", "addr_country"];

const COUNTRIES = ["US", "CA", "GB", "IE", "AU", "NZ", "IN", "AE", "SA", "QA", "SG", "MY", "PH", "ZA", "NG", "KE", "DE", "FR", "ES", "IT",
  "PT", "NL", "BE", "CH", "AT", "SE", "NO", "DK", "FI", "PL", "CZ", "RO", "GR", "TR", "IL", "PK", "BD", "LK", "HK", "JP", "KR", "MX", "BR", "AR", "CL", "CO"];

export function countryName(code: string): string {
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of(code) || code; } catch { return code; }
}

/** Street, City, Region Postcode, Country — the same line the server builds. */
export function oneLine(a: Address): string {
  const tail = [a.addr_region, a.addr_postcode].filter(Boolean).join(" ");
  return [a.addr_street, a.addr_city, tail, a.addr_country].filter(Boolean).join(", ");
}

export const hasParts = (a: Address) => ADDRESS_KEYS.some((k) => (a[k] || "").toString().trim());

/** Only the parts, with blanks sent as null (so clearing a field clears it). */
export function addressPayload(a: Address): Address {
  const out: Address = {};
  for (const k of ADDRESS_KEYS) out[k] = ((a[k] || "") as string).trim() || null;
  return out;
}

export default function AddressFields({ value, onChange, legacy, idPrefix = "addr", required }: {
  value: Address; onChange: (v: Address) => void; legacy?: string | null; idPrefix?: string; required?: boolean;
}) {
  const set = (k: keyof Address, v: string) => onChange({ ...value, [k]: v });
  const lbl = "block font-semibold text-ink-secondary mb-1";
  const field = "glass-input w-full px-3 py-2 rounded-lg text-ink-primary text-xs";
  const id = (k: string) => `${idPrefix}-${k}`;
  const current = (value.addr_country || "").toUpperCase();
  const options = current && !COUNTRIES.includes(current) ? [current, ...COUNTRIES] : COUNTRIES;
  return (
    <fieldset className="space-y-2.5">
      <legend className={`${lbl} text-xs`}>Address{required && <span className="text-rose-500"> *</span>}</legend>
      {legacy && !hasParts(value) && <p className="text-[11px] text-ink-tertiary">Saved as one line: <span className="text-ink-secondary">{legacy}</span>. Fill in the fields below to split it.</p>}
      <div><label htmlFor={id("street")} className="sr-only">Street</label>
        <input id={id("street")} value={value.addr_street || ""} onChange={(e) => set("addr_street", e.target.value)} placeholder="Street and number" className={field} /></div>
      <div className="grid grid-cols-2 gap-2.5">
        <div><label htmlFor={id("city")} className="sr-only">City</label><input id={id("city")} value={value.addr_city || ""} onChange={(e) => set("addr_city", e.target.value)} placeholder="City" className={field} /></div>
        <div><label htmlFor={id("region")} className="sr-only">State or region</label><input id={id("region")} value={value.addr_region || ""} onChange={(e) => set("addr_region", e.target.value)} placeholder="State or region" className={field} /></div>
        <div><label htmlFor={id("postcode")} className="sr-only">Postcode</label><input id={id("postcode")} value={value.addr_postcode || ""} onChange={(e) => set("addr_postcode", e.target.value)} placeholder="Postcode / ZIP" className={field} /></div>
        <div><label htmlFor={id("country")} className="sr-only">Country</label>
          <select id={id("country")} value={current} onChange={(e) => set("addr_country", e.target.value)} className={`${field} appearance-none`}>
            <option value="">Country</option>
            {options.map((c) => <option key={c} value={c}>{countryName(c)}</option>)}
          </select></div>
      </div>
    </fieldset>
  );
}
