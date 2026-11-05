import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { autoPrintForJob } from "../../lib/autoPrint";
import Button, { buttonClass } from "../../components/Button";
import { Badge } from "../../components/Card";
import { Input } from "../../components/Field";
import EmptyState from "../../components/EmptyState";

// Inquiries › Walk-ins (self check-in): today's queue at this location in arrival order, the
// people called to the counter, and overnight key drops. Call next texts the customer; staff
// convert after talking to them, and the job opens with everything the customer entered.

export type Walkin = {
  id: string; lead_id: string; queue_no: number; kind: "walk_in" | "key_drop"; state: string;
  name: string; phone?: string | null; issue?: string | null; asset: string;
  identifier_kind?: string | null; identifier?: string | null; reading_kind?: string | null; reading?: number | null;
  key_tag?: string | null; photos: number; point?: string | null; waited_min: number;
  seen_before?: { device_id: string; client_id?: string | null } | null; order_id?: string | null;
};
type Queue = { waiting: Walkin[]; called: Walkin[]; key_drops: Walkin[]; done: Walkin[] };

const READING: Record<string, string> = { mileage: "mi", hours: "hrs", odometer_hours: "hrs" };

function Row({ w, children }: { w: Walkin; children: React.ReactNode }) {
  const reading = w.reading != null ? `${Number(w.reading).toLocaleString()} ${READING[w.reading_kind || ""] || ""}`.trim() : "";
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-t border-slate-100 first:border-t-0">
      <span className="w-9 text-sm font-semibold text-ink-primary tabular-nums">{w.kind === "key_drop" ? <M n="key" c="text-base text-ink-tertiary" /> : `#${w.queue_no}`}</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold text-ink-primary flex items-center gap-1.5">
          {w.name}
          {w.seen_before && <Badge tone="info">Seen before</Badge>}
          {w.point && <span className="text-2xs font-normal text-ink-tertiary">· {w.point}</span>}
        </div>
        <div className="text-2xs text-ink-tertiary truncate">
          {[w.asset, reading, w.key_tag ? `key tag ${w.key_tag}` : "", w.photos ? `${w.photos} photo${w.photos > 1 ? "s" : ""}` : "", w.issue].filter(Boolean).join(" · ")}
        </div>
      </div>
      <span className="text-2xs text-ink-tertiary tabular-nums">{w.kind === "key_drop" ? "overnight" : `waiting ${w.waited_min}m`}</span>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

export default function WalkinsView() {
  const { can } = useMe();
  const navigate = useNavigate();
  const [q, setQ] = useState<Queue | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [code, setCode] = useState("");

  const load = useCallback(() => {
    api.get<Queue>("/checkins").then((r) => { setQ(r); window.dispatchEvent(new Event("tekly:checkins")); })
      .catch((e: any) => setMsg(e?.message || "Couldn't load the queue"));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, [load]);

  async function act(id: string, fn: () => Promise<any>, done?: (r: any) => void) {
    setBusy(id); setMsg(null);
    try { const r = await fn(); done?.(r); load(); }
    catch (e: any) { setMsg(e?.message || "Something went wrong"); }
    finally { setBusy(null); }
  }
  const call = (w: Walkin) => act(w.id, () => api.post<any>(`/checkins/${w.id}/call`, {}),
    (r) => setMsg(r?.texted ? `${w.name} was texted to come to the counter` : `Call ${w.name} to the counter`));
  const noShow = (w: Walkin) => act(w.id, () => api.post(`/checkins/${w.id}/no-show`, {}));
  const convert = (w: Walkin) => act(w.id, () => api.post<any>(`/checkins/${w.id}/convert`, {}), (r) => {
    if (!r?.already && r?.order_id) void autoPrintForJob(r.order_id);
    if (r?.order_id) navigate(`/orders?open=${r.order_id}`);
  });

  async function openByCode(e: React.FormEvent) {
    e.preventDefault();
    const c = code.trim();
    if (!c) return;
    try {
      const w = await api.get<Walkin>(`/checkins/by-code/${encodeURIComponent(c)}`);
      setCode("");
      setMsg(`Found ${w.name}${w.kind === "walk_in" ? ` (#${w.queue_no})` : ""}`);
      if (w.state === "waiting") await call(w);
    } catch { setMsg("No check-in with that code"); }
  }

  const canConvert = can("can_create_tickets");

  return (
    <div className="flex-1 min-h-0 overflow-y-auto space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <form onSubmit={openByCode} className="flex items-center gap-2">
          <label htmlFor="walkin-code" className="sr-only">Customer's check-in code</label>
          <Input id="walkin-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Scan the customer's code" className="!w-56" />
          <Button type="submit">Open</Button>
        </form>
        <a href="/settings?section=public" className={buttonClass("link", "md", "gap-1")}><M n="qr_code_2" c="text-sm" />Check-in QR codes</a>
        {msg && <p role="status" className="text-xs text-ink-secondary">{msg}</p>}
      </div>

      {!q && <div aria-hidden className="space-y-3"><span className="sk block h-24 rounded-2xl" /><span className="sk block h-16 rounded-2xl" /></div>}
      {q && (<>
        <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          <div className="px-4 py-2.5 text-xs font-semibold text-ink-primary border-b border-slate-100">Waiting <span className="font-normal text-ink-tertiary">· {q.waiting.length}</span></div>
          {q.waiting.length === 0 && <EmptyState size="inline" className="px-4" title="Nobody waiting. Customers check in by scanning the QR at the door." />}
          {q.waiting.map((w, i) => (
            <Row key={w.id} w={w}>
              <Button variant={i === 0 ? "primary" : "secondary"} disabled={busy === w.id} onClick={() => call(w)}>Call next</Button>
              <Button disabled={busy === w.id} onClick={() => noShow(w)}>No-show</Button>
            </Row>
          ))}
        </section>

        {q.called.length > 0 && (
          <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="px-4 py-2.5 text-xs font-semibold text-ink-primary border-b border-slate-100">Being served</div>
            {q.called.map((w) => (
              <Row key={w.id} w={w}>
                {canConvert && <Button variant="primary" disabled={busy === w.id} onClick={() => convert(w)}>Create work order</Button>}
                <Button disabled={busy === w.id} onClick={() => noShow(w)}>No-show</Button>
              </Row>
            ))}
          </section>
        )}

        {q.key_drops.length > 0 && (
          <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="px-4 py-2.5 text-xs font-semibold text-ink-primary border-b border-slate-100">Overnight drop-offs <span className="font-normal text-ink-tertiary">· {q.key_drops.length}</span></div>
            {q.key_drops.map((w) => (
              <Row key={w.id} w={w}>
                {canConvert && <Button variant="primary" disabled={busy === w.id} onClick={() => convert(w)}>Create work order</Button>}
              </Row>
            ))}
          </section>
        )}
      </>)}
    </div>
  );
}
