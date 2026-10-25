// ii144a — product barcodes on the client: the plain sentences the Stock tab shows after
// "Add missing barcodes", and the GS1 check the drawer uses to label a generated code.

/** GS1 mod-10 check digit for a numeric body (weights 3,1,3,… from the right). */
export function gtinCheckDigit(body: string): string {
  let sum = 0;
  for (let i = 0; i < body.length; i++) {
    const d = Number(body[body.length - 1 - i]);
    sum += (i % 2 === 0 ? 3 : 1) * d;
  }
  return String((10 - (sum % 10)) % 10);
}

/** True for an EAN-13 in the in-store range 20–29 with a valid check digit (a code the shop made). */
export function isInStoreEan13(code: string | null | undefined): boolean {
  const c = (code || "").trim();
  if (!/^\d{13}$/.test(c)) return false;
  const prefix = Number(c.slice(0, 2));
  return prefix >= 20 && prefix <= 29 && gtinCheckDigit(c.slice(0, 12)) === c[12];
}

/** What to tell the user after a bulk "add missing barcodes". */
export function missingBarcodesMessage(r: { generated: number; more?: boolean }, selected: number): string {
  if (!r.generated) {
    return selected ? "Every selected product already has a barcode." : "Every product already has a barcode.";
  }
  const n = r.generated;
  const base = `Added a barcode to ${n} product${n === 1 ? "" : "s"}. Existing barcodes weren't changed.`;
  return r.more ? `${base} Run it again to do the rest.` : base;
}

/** The main barcode first, then extra ones in the order they were added. */
export function sortBarcodes<T extends { is_main: boolean }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => Number(b.is_main) - Number(a.is_main));
}

// ii144b — every barcode type Tekly creates and scans (mirrors backend/services/barcode_types.py).
export const BARCODE_TYPES = [
  { key: "ean13", label: "EAN-13", hint: "13 digits; the check digit is added or verified. Our own codes start 20–29." },
  { key: "ean8", label: "EAN-8", hint: "8 digits, for small packs." },
  { key: "upca", label: "UPC-A", hint: "12 digits; the same product as the EAN-13 with a leading 0." },
  { key: "upce", label: "UPC-E", hint: "8-digit short UPC from the manufacturer; scans as its UPC-A." },
  { key: "itf14", label: "ITF-14", hint: "14 digits, printed on outer cartons." },
  { key: "itf", label: "Interleaved 2 of 5", hint: "An even number of digits." },
  { key: "code39", label: "Code 39", hint: "Capital letters, digits, spaces and - . $ / + %." },
  { key: "code128", label: "Code 128", hint: "Any letters and digits." },
  { key: "gs1_128", label: "GS1-128", hint: "GS1 data, e.g. (01) product number, (17) expiry, (10) batch." },
  { key: "qr", label: "QR code", hint: "Any text; scans with a phone camera." },
  { key: "datamatrix", label: "Data Matrix", hint: "Small square code; holds GS1 data too." },
  { key: "pdf417", label: "PDF417", hint: "Stacked code for longer text." },
] as const;
export type BarcodeType = (typeof BARCODE_TYPES)[number]["key"];

export const barcodeTypeLabel = (t?: string | null): string =>
  BARCODE_TYPES.find((x) => x.key === t)?.label || "Code 128";

/** Camera formats: the browser's BarcodeDetector names for the types above. */
export const DETECTOR_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "itf", "code_39", "code_128", "qr_code",
  "data_matrix", "pdf417", "codabar"] as const;

/** Keep only formats this browser's BarcodeDetector can read (older ones throw on unknown names). */
export function usableFormats(supported: string[] | null | undefined): string[] {
  const s = new Set(supported || []);
  return DETECTOR_FORMATS.filter((f) => s.has(f));
}
