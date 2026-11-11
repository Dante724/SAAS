// What the simple document setup can switch on: the "Show on documents" switches and the
// "+ Add detail" suggestions, per kind of document. Every field used here is one the server
// already fills for that kind of document (backend/routers/documents.py resolvers); where a kind
// can't show something, the switch is greyed out with the reason instead of being hidden.
import { addSection, fieldInMixedSection, hasSection, removeSection, setHiddenWhere, showsField } from "./simpleDoc";
import { chipLabel } from "./templateHtml";

export type DocKind = "invoice" | "receipt" | "estimate" | "work_order" | "label";
const JOB: DocKind[] = ["work_order", "estimate"];
const muted = "font-size:11px;color:#555";

export type Feature = {
  key: string; label: string;
  /** The fields that mean "this is already on the page" (in the shop's own layout). */
  detect: string[];
  /** Every field a section of the shop's own that is only about this thing can contain. */
  family: RegExp;
  /** The section to add for this kind of document, or null with the reason it can't show. */
  snippet: (k: DocKind) => string | null;
  reason: (k: DocKind) => string;
};

const sec = (key: string, inner: string, style = "") => `<div data-tk-section="${key}"${style ? ` style="${style}"` : ""}>${inner}</div>`;

export const FEATURES: Feature[] = [
  {
    key: "device", label: "Device and serial / IMEI", detect: ["{Asset Serial Number}", "{Asset IMEI}", "{Asset Model}"], family: /^\{Asset /,
    snippet: (k) => (JOB.includes(k) ? sec("device", "<p><strong>Device:</strong> {Asset Brand} {Asset Model} · Serial {Asset Serial Number} · IMEI {Asset IMEI}</p>") : null),
    reason: (k) => (k === "label" ? "Labels have their own layout." : k === "receipt" ? "Receipts are for sales, which have no device." : "Invoices don't carry device details; the job's work order does."),
  },
  {
    key: "warranty", label: "Warranty and terms", detect: ["{Order Warranty Expiration Date}", "{Company Note}"], family: /^\{(Order Warranty Expiration Date|Company Note)\}$/,
    snippet: (k) => (JOB.includes(k) ? sec("warranty", "<p>Warranty until {Order Warranty Expiration Date}.</p><p>{Company Note}</p>", `margin-top:12px;${muted}`)
      : k === "invoice" || k === "receipt" ? sec("warranty", "<p>{Company Note}</p>", `margin-top:12px;${muted}`) : null),
    reason: () => "Labels have their own layout.",
  },
  {
    key: "technician", label: "Technician", detect: ["{Order Assignee Name}"], family: /^\{Order Assignee /,
    snippet: (k) => (JOB.includes(k) || k === "invoice" ? sec("technician", "<p><strong>Technician:</strong> {Order Assignee Name}</p>") : null),
    reason: (k) => (k === "receipt" ? "Sales don't have a technician." : "Labels have their own layout."),
  },
  {
    key: "signature", label: "Customer signature", detect: ["Signature / Date"], family: /^$/,
    snippet: (k) => (k === "label" ? null : sec("signature", '<div style="border-bottom:1px solid #333;width:260px;height:28px"></div><div style="font-size:11px;color:#555;margin-top:3px">Signature / Date</div>', "margin-top:24px")),
    reason: () => "Labels have their own layout.",
  },
  {
    key: "tax", label: "Tax breakdown", detect: ["{Tax Summary}", "{Invoice Tax}", "{Tax Value}"], family: /^\{(Tax [^}]+|Invoice Tax)\}$/,
    snippet: (k) => (JOB.includes(k) ? sec("tax", "<p>{Tax Summary}</p>", "text-align:right") : k === "invoice" ? sec("tax", "<p>Tax {Invoice Tax}</p>", "text-align:right")
      : k === "receipt" ? sec("tax", "<p>Tax {Tax Value}</p>", "text-align:right") : null),
    reason: () => "Labels have their own layout.",
  },
  {
    key: "payqr", label: "Pay by QR", detect: ["{Order Payment QR}"], family: /^\{Order Payment (QR|URL)\}$/,
    snippet: (k) => (JOB.includes(k) ? sec("payqr", '<div style="display:inline-block;width:96px">{Order Payment QR}</div><div style="font-size:11px;color:#555">Scan to pay online</div>', "text-align:right") : null),
    reason: (k) => (k === "receipt" ? "A receipt is for a sale that's already paid." : k === "invoice" ? "Invoices carry a pay link instead: add “Payment link” from Add detail." : "Labels have their own layout."),
  },
];

/** Is this switch on for the page (our section, or the field shown by the shop's own layout)? */
export function featureOn(html: string, f: Feature): boolean {
  return hasSection(html, f.key) || showsField(html, f.detect);
}
/** A switch that is on because of a field inside a bigger section of the shop's own layout can't
 *  be turned off here (that would take other things away); the owner hides that section instead. */
export function featureLocked(html: string, f: Feature): string | null {
  if (hasSection(html, f.key) || !fieldInMixedSection(html, f.detect, f.family)) return null;
  return "It's part of this page's own layout: click that section on the page to hide it.";
}
/** Switch a feature on or off on one page. Off hides the shop's own sections that are only about
 *  it (so turning it back on brings exactly those back) and removes the section we added. */
export function setFeature(html: string, f: Feature, kind: DocKind, on: boolean): string {
  if (!on) return setHiddenWhere(removeSection(html, f.key), f.family, true);
  const shown = setHiddenWhere(html, f.family, false);
  if (featureOn(shown, f)) return shown;
  const snip = f.snippet(kind);
  return snip ? addSection(shown, snip) : html;
}

// ── "+ Add detail" ──
export type Detail = { label: string; tok: string };
export const SUGGESTED: Record<DocKind, Detail[]> = {
  work_order: [
    { label: "Device", tok: "{Asset Model}" }, { label: "Serial / IMEI", tok: "{Asset IMEI}" }, { label: "Technician", tok: "{Order Assignee Name}" },
    { label: "Warranty ends", tok: "{Order Warranty Expiration Date}" }, { label: "Deposit paid", tok: "{Order Prepayment}" }, { label: "Due date", tok: "{Order Due Date}" },
    { label: "Fault", tok: "{Order Malfunction}" }, { label: "Pay by QR", tok: "{Order Payment QR}" },
  ],
  estimate: [
    { label: "Device", tok: "{Asset Model}" }, { label: "Serial / IMEI", tok: "{Asset IMEI}" }, { label: "Valid until", tok: "{Order Due Date}" },
    { label: "Deposit to pay", tok: "{Order Prepayment}" }, { label: "Technician", tok: "{Order Assignee Name}" }, { label: "Fault", tok: "{Order Malfunction}" },
    { label: "Approve online", tok: "{Approve Button}" },
  ],
  invoice: [
    { label: "Due date", tok: "{Invoice Due Date}" }, { label: "Paid", tok: "{Invoice Paid}" }, { label: "Balance due", tok: "{Invoice Balance Due}" },
    { label: "Your tax number", tok: "{Legal Entity Tax ID}" }, { label: "Customer tax number", tok: "{Client Tax ID}" }, { label: "Bank account", tok: "{Legal Entity Bank Account}" },
    { label: "Payment link", tok: "{Invoice Payment URL}" }, { label: "Job number", tok: "{Order Number}" },
  ],
  receipt: [
    { label: "Customer phone", tok: "{Client Phone}" }, { label: "Payment status", tok: "{Payment Status}" }, { label: "Discount", tok: "{Total Discount}" },
    { label: "Barcode", tok: "{Barcode}" }, { label: "QR code", tok: "{QR Code}" }, { label: "Shop website", tok: "{Company Website}" },
  ],
  label: [
    { label: "Barcode", tok: "{Barcode}" }, { label: "QR code", tok: "{QR Code}" }, { label: "Price", tok: "{Price}" },
    { label: "Product title", tok: "{Product Title}" }, { label: "Article", tok: "{Product Article}" }, { label: "Shop name", tok: "{Company Name}" },
  ],
};

const detailKey = (tok: string) => `field:${tok}`;
export function detailSnippet(d: Detail): string {
  return `<p data-tk-section="${detailKey(d.tok).replace(/"/g, "&quot;")}"><strong>${d.label.replace(/</g, "&lt;")}:</strong> ${d.tok}</p>`;
}
/** "ours" = a line we added; "layout" = the shop's own layout already shows it; null = not on the page. */
export function detailState(html: string, tok: string): "ours" | "layout" | null {
  if (hasSection(html, detailKey(tok))) return "ours";
  return showsField(html, [tok]) ? "layout" : null;
}
/** Tap a detail: add a line for it, or take our line off again. A field the shop's own layout
 *  already shows is left alone (the popover says so). */
export function toggleDetail(html: string, d: Detail): string {
  const st = detailState(html, d.tok);
  if (st === "ours") return removeSection(html, detailKey(d.tok));
  if (st === "layout") return html;
  return addSection(html, detailSnippet(d));
}
export const detailFor = (tok: string, label?: string): Detail => ({ tok, label: label || chipLabel(tok) });
