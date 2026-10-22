// D1 — click-to-call. Shown only when the shop has calling turned on (Settings → Integrations →
// Calls & telephony) and the person's role has "Place phone calls". With Twilio it rings the employee's own phone first, then connects the
// customer; with the simulated provider it just logs the call.
import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { alertDialog } from "../lib/dialog";
import { useMe } from "../context/Me";

let enabledOnce: Promise<boolean> | null = null;   // one config read per page load
function callingEnabled(): Promise<boolean> {
  enabledOnce ??= api.get<{ enabled: boolean }>("/telephony/config").then((c) => !!c.enabled).catch(() => false);
  return enabledOnce;
}
export function _resetCallingCache() { enabledOnce = null; }   // tests

export default function CallButton({ phone, clientId }: { phone?: string | null; clientId?: string }) {
  const { can } = useMe();
  const allowed = can("can_make_calls");
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!allowed) return;
    let live = true; callingEnabled().then((v) => live && setOn(v)); return () => { live = false; };
  }, [allowed]);
  if (!allowed || !on || !(phone || "").trim()) return null;
  async function call() {
    setBusy(true);
    try {
      const r = await api.post<{ provider: string; test_mode?: boolean }>("/telephony/call", { to_number: phone, client_id: clientId });
      await alertDialog(r.provider === "twilio"
        ? (r.test_mode ? "Test call placed with Twilio test credentials. Nothing is dialled." : "Calling your phone now. Pick up to be connected to the customer.")
        : "Call logged (simulated provider).", "Call");
    } catch (e: any) {
      await alertDialog(e?.message || "Couldn't place the call", "Call");
    } finally { setBusy(false); }
  }
  return (
    <button type="button" onClick={call} disabled={busy} aria-label={`Call ${phone}`}
      className="px-2.5 py-1 rounded-full text-[11px] font-bold border border-primary/30 bg-primary/5 text-primary hover:bg-primary/10 disabled:opacity-60 flex items-center gap-1">
      <M n="smartphone" c="text-[13px]" />{busy ? "Calling…" : "Call"}
    </button>
  );
}
