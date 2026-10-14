// Which serial-type identifier fields an asset form shows is NOT universal — it's
// decided by the org's industry template. The backend owns the rule
// (services/industry.py → describe().identifier = {primary, allowed}); e.g. Phone
// Repair allows imei+serial, Auto allows vin, Marine allows hin+vin+engine_serial.
// This maps the allowed identifier *kinds* to the concrete Device columns we can
// persist, so both the asset form and the WO/estimate create flow render exactly
// the identifiers that make sense for the current template — nothing more.

export type IdKind = "serial" | "imei" | "vin" | "hin";

// Only kinds that have a real Device column are renderable here. Others the
// backend may list (barcode, asset_tag, engine_serial) are scan/lookup or
// custom-field concerns, not standalone inputs — skip them.
const FIELDS: Record<IdKind, { label: string; placeholder: string }> = {
  serial: { label: "Serial number", placeholder: "SN-…" },
  imei: { label: "IMEI", placeholder: "35-209900-176148-1" },
  vin: { label: "VIN", placeholder: "1HGCM82633A004352" },
  hin: { label: "HIN", placeholder: "ABC12345D404" },
};

const RENDERABLE = new Set<string>(Object.keys(FIELDS));

export type IdentifierField = { kind: IdKind; label: string; placeholder: string };

// The identifier fields to render, in template order. `primary` (when given) is
// pulled to the front. Falls back to a plain Serial when a template lists no
// renderable identifier — every asset still needs one identity field.
export function identifierFields(allowed?: string[], primary?: string): IdentifierField[] {
  const list = (allowed || []).filter((k) => RENDERABLE.has(k)) as IdKind[];
  const kinds = list.length ? list : (["serial"] as IdKind[]);
  const ordered = primary && kinds.includes(primary as IdKind)
    ? [primary as IdKind, ...kinds.filter((k) => k !== primary)]
    : kinds;
  return ordered.map((kind) => ({ kind, ...FIELDS[kind] }));
}
