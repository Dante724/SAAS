// WS-C #20 — print the shop's "print when a job is created" labels (usually the device label)
// right after a job is saved. Silent when no template is set up; never blocks the save.
import { api } from "../api/client";
import { printHtmlInFrame } from "./safeHtml";

type Item = { template_id: string; name: string; count: number; html: string };

export async function autoPrintAfterCreate(orderId: string, print: (html: string) => void = printHtmlInFrame): Promise<number> {
  try {
    const r = await api.get<{ items: Item[] }>(`/auto-print/order-created/${encodeURIComponent(orderId)}`);
    const items = (r?.items || []).filter((i) => i.count > 0 && i.html);
    items.forEach((i) => print(i.html));
    return items.length;
  } catch {
    return 0;   // printing is a convenience; the job is already saved
  }
}
