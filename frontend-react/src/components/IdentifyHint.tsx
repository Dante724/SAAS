import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";

// Shown under an IMEI / VIN / serial field at intake. As staff type or scan, it checks the value:
// a likely typo gets a gentle warning, and a device this shop has seen before shows who owns it,
// how many jobs it had and whether it's still under warranty — with one click to reuse it
// instead of creating a duplicate. It never blocks saving.
export type IdMatch = {
  id: string; brand?: string; model?: string; color?: string; watch_out?: string;
  client?: { id: string; name?: string; phone?: string } | null; same_client: boolean; jobs: number;
  last_order?: { order_number: string; status?: string; created_at?: string; warranty_until?: string; under_warranty?: boolean } | null;
};
type Res = { kind: string; normalized: string | null; problem: string | null; matches: IdMatch[] };

const KIND_LABEL: Record<string, string> = { imei: "IMEI", vin: "VIN", hin: "HIN", serial: "serial" };
const fmt = (s?: string) => (s ? new Date(s).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "");

export default function IdentifyHint({ value, kind, clientId, usingId, onUse, onUndo }: {
  value: string; kind?: string; clientId?: string | null; usingId: string | null;
  onUse: (m: IdMatch) => void; onUndo: () => void;
}) {
  const [res, setRes] = useState<Res | null>(null);
  useEffect(() => {
    const v = value.trim();
    if (v.length < 5) { setRes(null); return; }
    const t = setTimeout(() => {
      api.post<Res>("/assets/identify", { value: v, kind, client_id: clientId || null }).then(setRes).catch(() => setRes(null));
    }, 400);
    return () => clearTimeout(t);
  }, [value, kind, clientId]);
  if (!res) return null;
  const m = res.matches[0];
  if (usingId && m && m.id === usingId) {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
        <M n="check_circle" c="text-sm" />
        <span className="flex-1">Using the existing {[m.brand, m.model].filter(Boolean).join(" ") || "device"} — no duplicate will be created.</span>
        <button type="button" onClick={onUndo} className="font-semibold underline">Undo</button>
      </div>
    );
  }
  return (
    <div className="mt-2 space-y-1.5">
      {res.problem && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <M n="warning" c="text-sm mt-px" /><span>{res.problem} You can still save it.</span>
        </div>
      )}
      {m && (
        <div className={`rounded-xl border px-3 py-2.5 text-xs ${m.same_client || !clientId ? "border-blue-200 bg-blue-50/70" : "border-amber-200 bg-amber-50"}`}>
          <div className="flex items-start gap-2">
            <M n="history" c={`text-base ${m.same_client || !clientId ? "text-primary" : "text-amber-700"}`} />
            <div className="flex-1 space-y-0.5">
              <div className="font-bold text-ink-primary">
                Seen before: {[m.brand, m.model].filter(Boolean).join(" ") || `this ${KIND_LABEL[res.kind] || "device"}`}
                {m.client?.name && <span className="font-semibold text-ink-secondary"> · {m.client.name}</span>}
              </div>
              <div className="text-ink-secondary">
                {m.jobs} previous job{m.jobs === 1 ? "" : "s"}
                {m.last_order && <> · last {m.last_order.order_number} ({m.last_order.status}, {fmt(m.last_order.created_at)})</>}
              </div>
              {m.last_order?.under_warranty && <div className="font-semibold text-emerald-700">Under warranty until {fmt(m.last_order.warranty_until)} — consider opening this as a warranty job.</div>}
              {clientId && !m.same_client && m.client && <div className="font-semibold text-amber-800">Registered to a different customer{m.client.phone ? ` (${m.client.phone})` : ""} — check before continuing.</div>}
              {m.watch_out && <div className="font-semibold text-rose-700">⚠ {m.watch_out}</div>}
            </div>
            <button type="button" onClick={() => onUse(m)}
              className="self-center px-3 py-1.5 rounded-lg bg-primary text-white text-[11px] font-bold hover:bg-primary-container whitespace-nowrap">Use this device</button>
          </div>
        </div>
      )}
    </div>
  );
}
