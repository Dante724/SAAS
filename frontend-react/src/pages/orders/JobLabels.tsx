// Tekly Go pilot: print a sheet of QR labels to tape on job tickets. Each QR opens the job in
// Tekly Go (GET /api/pilot/job-labels → PDF). Printing also marks those jobs as the pilot's
// jobs for the report (docs/pilot/tekly-go-shop-pilot.md).
import { useState } from "react";
import { openAuthedDoc } from "../../api/client";
import { alertDialog } from "../../lib/dialog";
import { Gate } from "../../context/Me";

export type LabelPick = { orderIds?: string[]; assignedToId?: string };

/** US and Canadian printers load Letter; everyone else A4. */
export function paperFor(lang: string | undefined): "a4" | "letter" {
  return /^(en|es|fr)-(US|CA|MX|PH)$/i.test(lang || "") ? "letter" : "a4";
}

export function jobLabelsPath(pick: LabelPick, paper: "a4" | "letter"): string {
  const p = new URLSearchParams({ paper });
  if (pick.orderIds?.length) p.set("order_ids", pick.orderIds.join(","));
  else if (pick.assignedToId) p.set("assigned_to_id", pick.assignedToId);
  return `/pilot/job-labels?${p}`;
}

export function JobLabelsButton({ pick, label, className }: { pick: LabelPick; label: string; className?: string }) {
  const [busy, setBusy] = useState(false);
  async function print() {
    setBusy(true);
    try {
      await openAuthedDoc(jobLabelsPath(pick, paperFor(typeof navigator !== "undefined" ? navigator.language : "")));
    } catch (e: any) {
      await alertDialog(e?.message || "Couldn't make the labels.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Gate perm="can_create_tickets">
      <button type="button" disabled={busy} onClick={print} className={className} title="A sheet of QR labels that open each job in Tekly Go">
        {busy ? "Preparing…" : label}
      </button>
    </Gate>
  );
}
