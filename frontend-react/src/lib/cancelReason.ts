import { api } from "../api/client";
import { selectDialog } from "./dialog";

// Ask why a job is being cancelled before it moves into a dropped-group status. The answer feeds
// the "Cancellations by reason" report; options come from Settings → Directories → Cancellation
// reasons. Resolves to the reason ("" if skipped) or null if the user backed out of cancelling.
export async function askCancelReason(statusName?: string, count = 1): Promise<string | null> {
  const r = await api.get<{ items: { value: string }[] }>("/directories/by-key/cancel_reason/items").catch(() => ({ items: [] }));
  const as = statusName ? `marked “${statusName}”` : "cancelled";
  return selectDialog(count > 1 ? `Why are these ${count} jobs being ${as}?` : `Why is this job being ${as}?`,
    (r.items || []).map((i) => i.value), { title: "Reason", confirmLabel: "Save reason", allowOther: true, danger: true });
}

type St = { key?: string; name?: string; status_group?: string };
export const droppedStatus = (statuses: St[], key: string): St | undefined =>
  statuses.find((s) => s.key === key && s.status_group === "dropped");
