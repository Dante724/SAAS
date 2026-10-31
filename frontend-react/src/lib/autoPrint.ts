// Documents switched on to print by themselves (the printer button on the new-job, close-job and
// POS screens, or Settings › Documents). The server renders them; each prints through a hidden
// frame, so with the browser's print dialog turned off they come straight out of the printer.
// Never blocks or fails the job or sale: a print problem only shows up as a missing print.
import { api } from "../api/client";
import { printHtmlInFrame } from "./safeHtml";

export type AutoPrintEvent = "create" | "close" | "sale";

async function printAll(url: string, print: (html: string) => void): Promise<number> {
  try {
    const r = await api.post<{ docs: { name: string; html: string }[] }>(url, {});
    for (const d of r?.docs || []) print(d.html);
    return r?.docs?.length || 0;
  } catch {
    return 0;
  }
}

export function autoPrintForJob(orderId: string, event: "create" | "close" = "create",
                                print: (html: string) => void = printHtmlInFrame): Promise<number> {
  return printAll(`/documents/auto-print/${orderId}?event=${event}`, print);
}

export function autoPrintForSale(saleId: string, print: (html: string) => void = printHtmlInFrame): Promise<number> {
  return printAll(`/documents/auto-print-sale/${saleId}`, print);
}

/** After a status change: a job that just closed prints what's switched on for closing. */
export function autoPrintIfClosed(orderId: string, r: { is_closing?: boolean } | null | undefined,
                                  print: (html: string) => void = printHtmlInFrame): Promise<number> {
  return r?.is_closing ? autoPrintForJob(orderId, "close", print) : Promise.resolve(0);
}
