import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import Switch from "../../components/Switch";
import { friendlyDate } from "../orders/look";
import { tint, hueText } from "../../lib/tint";
import { Drawer, ErrorNote, MoreMenu, btnDanger, btnGhost, btnPrimary, inputCls, labelCls } from "../invoices/billing";

// B15 — Settings › Integrations › Webhooks. Add, edit and remove endpoints, pick events, see each
// endpoint's deliveries (and send one again), send a test event, and rotate the signing secret.
// The secret is shown once, when it is made.

export type Endpoint = {
  id: string; url: string; description?: string | null; events: string; active: boolean;
  secret_hint?: string | null; previous_secret_expires_at?: string | null;
  failing_since?: string | null; disabled_reason?: string | null; created_at?: string | null;
};
export type Delivery = {
  id: string; endpoint_id: string; event: string; status: string; response_code?: number | null;
  attempts: number; error?: string | null; created_at?: string | null; delivered_at?: string | null;
  next_attempt_at?: string | null; payload?: unknown;
};
type CatalogueItem = { name: string; description: string };
type ListResponse = { items: Endpoint[]; available_events: string[]; event_catalogue?: CatalogueItem[]; max_endpoints?: number };
type Fresh = { secret: string; rotated: boolean };

const HUE: Record<string, string> = { ok: "#1D9E75", warn: "#BA7517", bad: "#c2410c", off: "#64748b", info: "#2c72d9" };

function Pill({ hue, children }: { hue: string; children: React.ReactNode }) {
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>{children}</span>;
}

/** What a delivery row says, in words. */
export function deliveryState(d: Delivery): { label: string; hue: string } {
  if (d.status === "sent") return { label: "Delivered", hue: HUE.ok };
  if (d.status === "pending" || d.status === "sending") return { label: "Sending", hue: HUE.info };
  if (d.status === "abandoned") return { label: "Not sent", hue: HUE.off };
  if (d.status === "failed") return d.next_attempt_at ? { label: "Will retry", hue: HUE.warn } : { label: "Failed", hue: HUE.bad };
  return { label: "Unknown", hue: HUE.off };
}

function endpointState(e: Endpoint): { label: string; hue: string } {
  if (!e.active && e.disabled_reason) return { label: "Turned off", hue: HUE.bad };
  if (!e.active) return { label: "Off", hue: HUE.off };
  if (e.failing_since) return { label: "Failing", hue: HUE.warn };
  return { label: "On", hue: HUE.ok };
}

// API times are UTC without a zone marker.
const utc = (iso: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");

const eventsText = (events: string) => {
  if (!events || events.trim() === "*") return "All events";
  const n = events.split(",").filter((s) => s.trim()).length;
  return `${n} ${n === 1 ? "event" : "events"}`;
};

export default function WebhooksSettings() {
  const [data, setData] = useState<ListResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Endpoint | "new" | null>(null);
  const [fresh, setFresh] = useState<Fresh | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 2600); };

  const load = () => {
    setErr(null);
    return api.get<ListResponse>("/webhooks").then((r) => { setData(r); return r; })
      .catch((e) => { setErr(e.message || "Couldn't load webhooks"); return null; });
  };
  useEffect(() => { load(); }, []);

  const catalogue: CatalogueItem[] = data?.event_catalogue?.length
    ? data.event_catalogue
    : (data?.available_events || []).map((n) => ({ name: n, description: n }));
  const items = data?.items || [];
  const atCap = data?.max_endpoints != null && items.length >= data.max_endpoints;

  async function remove(e: Endpoint) {
    if (!(await confirmDialog(`Delete the webhook to ${e.url}? Nothing more is sent to it. Its delivery log is kept.`, { danger: true, confirmLabel: "Delete" }))) return;
    try { await api.del(`/webhooks/${e.id}`); setOpen(null); flash("Webhook deleted"); load(); }
    catch (x: any) { flash(x.message || "Couldn't delete the webhook"); }
  }
  async function sendTest(e: Endpoint) {
    try {
      const d = await api.post<Delivery>(`/webhooks/${e.id}/test`, {});
      flash(d.status === "sent" ? `Test event delivered (HTTP ${d.response_code})` : `Test event failed: ${d.error || "no answer"}`);
    } catch (x: any) { flash(x.message || "Couldn't send the test event"); }
  }
  async function rotate(e: Endpoint) {
    if (!(await confirmDialog("Make a new signing secret? The current one keeps working for 24 hours, so you have time to update your receiver.", { confirmLabel: "Make new secret" }))) return;
    try { const r = await api.post<Endpoint & { secret: string }>(`/webhooks/${e.id}/rotate`, {}); setFresh({ secret: r.secret, rotated: true }); load(); }
    catch (x: any) { flash(x.message || "Couldn't rotate the secret"); }
  }

  const addBtn = <button onClick={() => setOpen("new")} disabled={atCap} className={btnPrimary}><M n="add" c="text-sm" />Add endpoint</button>;

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="p-3.5 border-b border-slate-100">
        <h3 className="text-sm font-semibold text-ink-primary">Webhooks</h3>
        {data && <div className="text-xs text-ink-tertiary mt-0.5 tabular-nums">{items.length} of {data.max_endpoints ?? 10} endpoints</div>}
        {items.length > 0 && <div className="mt-3">{addBtn}</div>}
      </div>

      {!data && !err && <div className="p-4 text-xs text-ink-tertiary">Loading webhooks…</div>}
      {err && <div role="alert" className="p-4 text-xs text-rose-600 flex items-center gap-2"><M n="error" c="text-sm" />{err}<button onClick={load} className="ml-auto font-semibold text-primary hover:underline">Retry</button></div>}

      {data && (
        <div className="p-3.5 space-y-3">
          {items.length === 0 && (
            <div className="py-4 text-center space-y-3">
              <p className="text-xs text-ink-secondary">No webhooks yet. Add an endpoint to send new jobs, invoices and bookings to your own systems as they happen.</p>
              {addBtn}
            </div>
          )}
          {items.length > 0 && (
            <ul className="divide-y divide-slate-100 border border-slate-200/80 rounded-xl overflow-hidden">
              {items.map((e) => {
                const st = endpointState(e);
                return (
                  <li key={e.id} className="flex items-center gap-1 pr-1.5 hover:bg-slate-50">
                    <button type="button" onClick={() => setOpen(e)} aria-label={`Open webhook ${e.url}`} className="flex-1 min-w-0 flex items-center gap-2.5 px-3 py-2.5 text-left">
                      <M n="link" c="text-base text-ink-tertiary flex-shrink-0" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-ink-primary truncate">{e.description || e.url}</span>
                        <span className="block text-xs text-ink-tertiary truncate">{e.description ? `${e.url} · ` : ""}{eventsText(e.events)}</span>
                      </span>
                      <Pill hue={st.hue}>{st.label}</Pill>
                    </button>
                    <MoreMenu label={`More actions for ${e.url}`} items={[
                      { label: "Edit", icon: "edit", onClick: () => setOpen(e) },
                      { label: "Send test event", icon: "send", onClick: () => sendTest(e) },
                      { label: "New signing secret", icon: "lock", onClick: () => rotate(e) },
                      { label: "Delete", icon: "delete", danger: true, onClick: () => remove(e) },
                    ]} />
                  </li>
                );
              })}
            </ul>
          )}
          {atCap && <p className="text-xs text-ink-tertiary">You've reached {data.max_endpoints} endpoints. Delete one to add another.</p>}
          <p className="text-xs text-ink-tertiary leading-relaxed">Each delivery is signed. Check the Tekly-Signature header with your signing secret before you trust it.</p>
        </div>
      )}

      {open && data && (
        <EndpointDrawer endpoint={open === "new" ? null : open} catalogue={catalogue}
          onClose={() => setOpen(null)} onDelete={remove} onTest={sendTest} onRotate={rotate}
          onSaved={async (m, created) => {
            flash(m);
            const r = await load();
            if (created) { setFresh({ secret: created.secret, rotated: false }); setOpen(r?.items.find((x) => x.id === created.id) || null); }
            else if (open !== "new") setOpen(r?.items.find((x) => x.id === (open as Endpoint).id) || null);
          }} />
      )}
      {fresh && <SecretOnce fresh={fresh} onClose={() => setFresh(null)} />}
      {msg && createPortal(<div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer z-[95]">{msg}</div>, document.body)}
    </div>
  );
}

export function EndpointDrawer({ endpoint, catalogue, onClose, onSaved, onDelete, onTest, onRotate }: {
  endpoint: Endpoint | null; catalogue: CatalogueItem[]; onClose: () => void;
  onSaved: (m: string, created?: { id: string; secret: string }) => void;
  onDelete: (e: Endpoint) => void; onTest: (e: Endpoint) => Promise<void>; onRotate: (e: Endpoint) => void;
}) {
  const initial = endpoint?.events && endpoint.events.trim() !== "*" ? endpoint.events.split(",").map((s) => s.trim()).filter(Boolean) : [];
  const [url, setUrl] = useState(endpoint?.url || "");
  const [description, setDescription] = useState(endpoint?.description || "");
  const [all, setAll] = useState(!endpoint || endpoint.events.trim() === "*");
  const [picked, setPicked] = useState<string[]>(initial);
  const [active, setActive] = useState(endpoint?.active !== false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toggle = (n: string) => setPicked((p) => (p.includes(n) ? p.filter((x) => x !== n) : [...p, n]));

  async function save() {
    if (!url.trim()) { setErr("Add the endpoint URL"); return; }
    if (!all && picked.length === 0) { setErr("Pick at least one event, or send all events"); return; }
    setBusy(true); setErr(null);
    const body = { url: url.trim(), description: description.trim() || null, events: all ? "*" : picked };
    try {
      if (endpoint) {
        await api.patch(`/webhooks/${endpoint.id}`, { ...body, active });
        onSaved("Webhook saved");
      } else {
        const r = await api.post<Endpoint & { secret: string }>("/webhooks", body);
        onSaved("Webhook added", { id: r.id, secret: r.secret });
      }
    } catch (e: any) { setErr(e.message || "Couldn't save the webhook"); } finally { setBusy(false); }
  }

  const title = endpoint ? "Edit webhook" : "Add webhook";
  return createPortal(
    <Drawer z={90} width={520} label={title} title={title} sub={endpoint ? endpoint.url : undefined} onClose={onClose}
      footer={<>
        {endpoint && <button onClick={() => onDelete(endpoint)} className={`${btnDanger} mr-auto`}>Delete</button>}
        <button onClick={onClose} className={btnGhost}>Cancel</button>
        <button onClick={save} disabled={busy} className={btnPrimary}>{busy ? "Saving…" : endpoint ? "Save" : "Add webhook"}</button>
      </>}>
      {endpoint?.disabled_reason && !endpoint.active && (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 leading-relaxed">{endpoint.disabled_reason}. Fix the endpoint, send a test event, then turn it back on.</p>
      )}
      <div><label htmlFor="wh-url" className={labelCls}>Endpoint URL</label>
        <input id="wh-url" value={url} onChange={(e) => setUrl(e.target.value)} maxLength={500} placeholder="https://example.com/tekly" className={inputCls} />
        <p className="text-xs text-ink-tertiary mt-1">A public https address.</p></div>
      <div><label htmlFor="wh-desc" className={labelCls}>Description</label>
        <input id="wh-desc" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={200} placeholder="e.g. Accounting sync" className={inputCls} /></div>

      <fieldset>
        <legend className={labelCls}>Events</legend>
        <div className="flex gap-4 mb-2">
          <label className="inline-flex items-center gap-2 text-sm text-ink-primary"><input type="radio" name="wh-all" checked={all} onChange={() => setAll(true)} />All events</label>
          <label className="inline-flex items-center gap-2 text-sm text-ink-primary"><input type="radio" name="wh-all" checked={!all} onChange={() => setAll(false)} />Choose events</label>
        </div>
        {!all && (
          <ul className="border border-slate-200/80 rounded-xl divide-y divide-slate-100 max-h-72 overflow-y-auto">
            {catalogue.map((c) => (
              <li key={c.name}>
                <label className="flex items-start gap-2.5 px-3 py-2 cursor-pointer hover:bg-slate-50">
                  <input type="checkbox" className="mt-0.5" checked={picked.includes(c.name)} onChange={() => toggle(c.name)} aria-label={c.name} />
                  <span className="min-w-0">
                    <span className="block text-sm text-ink-primary">{c.description}</span>
                    <span className="block font-mono text-[11px] text-ink-tertiary">{c.name}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      {endpoint && (
        <div className="flex items-center gap-3 text-sm text-ink-primary">
          <Switch label="Send events to this endpoint" on={active} onChange={setActive} />Send events to this endpoint
        </div>
      )}
      <ErrorNote msg={err} />

      {endpoint && (
        <section className="pt-2 border-t border-slate-100 space-y-2">
          <h4 className="text-sm font-semibold text-ink-primary">Signing secret</h4>
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-ink-secondary">{endpoint.secret_hint || "Hidden"}</span>
            <button onClick={() => onRotate(endpoint)} className={`${btnGhost} ml-auto`}><M n="lock" c="text-sm" />New secret</button>
          </div>
          {endpoint.previous_secret_expires_at && <p className="text-xs text-ink-tertiary">The previous secret also signs deliveries until {utc(endpoint.previous_secret_expires_at).toLocaleString()}.</p>}
        </section>
      )}
      {endpoint && <DeliveryLog endpoint={endpoint} onTest={onTest} />}
    </Drawer>, document.body);
}

export function DeliveryLog({ endpoint, onTest }: { endpoint: Endpoint; onTest: (e: Endpoint) => Promise<void> }) {
  const [rows, setRows] = useState<Delivery[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Delivery | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<{ items: Delivery[] }>(`/webhooks/deliveries?endpoint_id=${encodeURIComponent(endpoint.id)}&limit=25`)
    .then((r) => setRows(r.items || [])).catch((e) => { setErr(e.message || "Couldn't load deliveries"); setRows([]); });
  useEffect(() => { load(); }, [endpoint.id]);

  async function expand(d: Delivery) {
    if (openId === d.id) { setOpenId(null); return; }
    setOpenId(d.id); setDetail(null);
    try { setDetail(await api.get<Delivery>(`/webhooks/deliveries/${d.id}`)); } catch { setDetail(null); }
  }
  async function redeliver(d: Delivery) {
    setBusy(d.id); setErr(null);
    try { await api.post(`/webhooks/deliveries/${d.id}/redeliver`, {}); await load(); }
    catch (e: any) { setErr(e.message || "Couldn't send it again"); } finally { setBusy(null); }
  }
  async function test() { setBusy("test"); await onTest(endpoint); await load(); setBusy(null); }

  return (
    <section className="pt-2 border-t border-slate-100 space-y-2">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-semibold text-ink-primary">Recent deliveries</h4>
        <button onClick={test} disabled={busy === "test"} className={`${btnGhost} ml-auto`}><M n="send" c="text-sm" />{busy === "test" ? "Sending…" : "Send test event"}</button>
      </div>
      <ErrorNote msg={err} />
      {!rows && <p className="text-xs text-ink-tertiary">Loading deliveries…</p>}
      {rows && rows.length === 0 && <p className="text-xs text-ink-tertiary">Nothing sent yet. Send a test event to check the endpoint answers.</p>}
      {rows && rows.length > 0 && (
        <ul className="border border-slate-200/80 rounded-xl divide-y divide-slate-100">
          {rows.map((d) => {
            const st = deliveryState(d);
            const canResend = d.status === "sent" || d.status === "failed" || d.status === "abandoned";
            return (
              <li key={d.id}>
                <div className="flex items-center gap-2 pr-1.5 hover:bg-slate-50">
                  <button type="button" onClick={() => expand(d)} aria-expanded={openId === d.id} className="flex-1 min-w-0 flex flex-wrap items-center gap-x-2 gap-y-0.5 px-3 py-2 text-left">
                    <span className="font-mono text-xs text-ink-primary break-all">{d.event}</span>
                    <Pill hue={st.hue}>{st.label}</Pill>
                    <span className="basis-full sm:basis-auto sm:ml-auto text-xs text-ink-tertiary tabular-nums whitespace-nowrap">{d.response_code ? `HTTP ${d.response_code} · ` : ""}{friendlyDate(d.created_at)}</span>
                  </button>
                  {canResend && <button onClick={() => redeliver(d)} disabled={busy === d.id} className="text-xs font-semibold text-primary hover:underline px-1 disabled:opacity-50">{busy === d.id ? "Sending…" : "Redeliver"}</button>}
                </div>
                {openId === d.id && (
                  <div className="px-3 pb-3 space-y-1.5">
                    <p className="text-xs text-ink-secondary tabular-nums">
                      {d.attempts} {d.attempts === 1 ? "attempt" : "attempts"}{d.error ? ` · ${d.error}` : ""}
                      {d.next_attempt_at ? ` · next try ${utc(d.next_attempt_at).toLocaleString()}` : ""}
                    </p>
                    <pre className="text-[11px] font-mono bg-slate-50 border border-slate-200 rounded-lg p-2 overflow-x-auto max-h-64 text-ink-primary">{detail ? JSON.stringify(detail.payload, null, 2) : "Loading…"}</pre>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function SecretOnce({ fresh, onClose }: { fresh: Fresh; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(fresh.secret); setCopied(true); } catch { setCopied(false); }
  }
  return createPortal(
    <Drawer z={92} width={448} label="Signing secret" title="Signing secret" onClose={onClose}
      footer={<button onClick={onClose} className={btnPrimary}>I've saved it</button>}>
      <p className="text-sm text-ink-secondary">Copy it now and keep it with your receiver. You won't be able to see it again.</p>
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
        <div className="font-mono text-xs text-ink-primary break-all select-all" data-testid="fresh-secret">{fresh.secret}</div>
      </div>
      <button onClick={copy} className={btnGhost}><M n={copied ? "check" : "content_copy"} c="text-sm" />{copied ? "Copied" : "Copy secret"}</button>
      {fresh.rotated && <p className="text-xs text-ink-tertiary">The old secret keeps signing deliveries for 24 hours, so you have time to swap this one in.</p>}
    </Drawer>, document.body);
}
