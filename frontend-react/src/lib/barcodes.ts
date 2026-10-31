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
