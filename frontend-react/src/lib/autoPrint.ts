// Templates set to "print automatically when a job is created" (Settings › Documents, label
// templates). The server renders them for the new job; each opens the browser print dialog.
// Never blocks or fails job creation: a print problem only shows up as a missing print.
import { api } from "../api/client";
import { printHtmlInFrame } from "./safeHtml";

export async function autoPrintForJob(orderId: string, event: "create" | "close" = "create",
                                      print: (html: string) => void = printHtmlInFrame): Promise<number> {
  try {
    const r = await api.post<{ docs: { name: string; html: string }[] }>(`/documents/auto-print/${orderId}?event=${event}`, {});
    for (const d of r?.docs || []) print(d.html);
    return r?.docs?.length || 0;
  } catch {
    return 0;
  }
}
