// Print the job ticket and get the customer's tracking link, for the "job created" screen.
// The ticket is the shop's own work-order document template (Settings → Document templates),
// rendered by the server for this job and printed through a frame that can't run script.
import { api } from "../../api/client";
import { printHtmlInFrame } from "../../lib/safeHtml";

type Tpl = { id: string; name: string; doc_type: string; body_html: string; is_default?: boolean; page_size?: string;
  page_w_mm?: number | null; page_h_mm?: number | null; margin_mm?: number | null; font_family?: string };

/** The template to print at drop-off: the default work-order document, else any work-order one. */
export function pickTicketTemplate(items: Tpl[]): Tpl | null {
  const wo = items.filter((t) => t.doc_type === "work_order");
  return wo.find((t) => t.is_default) || wo[0] || null;
}

export async function printTicket(orderId: string): Promise<void> {
  const r = await api.get<{ items: Tpl[] }>(`/documents/templates?order_id=${encodeURIComponent(orderId)}`);
  const t = pickTicketTemplate(r.items || []);
  if (!t) throw new Error("No work-order document is set up. Add one in Settings → Document templates.");
  const v = await api.post<{ html: string }>("/documents/print-view", {
    body_html: t.body_html, doc_type: t.doc_type, order_id: orderId, page_size: t.page_size || "a4",
    page_w_mm: t.page_w_mm ?? null, page_h_mm: t.page_h_mm ?? null, margin_mm: t.margin_mm ?? null, font_family: t.font_family || "Inter",
  });
  printHtmlInFrame(v.html);
}

/** The customer's tracking page for this job, as a full URL. */
export async function trackingUrl(orderId: string): Promise<string> {
  const r = await api.post<{ path: string }>(`/orders/${encodeURIComponent(orderId)}/public-link`, {});
  return new URL(r.path, window.location.origin).toString();
}
