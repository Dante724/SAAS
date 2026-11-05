import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import Pager from "../../components/Pager";
import InquiryDrawer from "./InquiryDrawer";
import InquiryFields from "./InquiryFields";
import WalkinsView from "./WalkinsView";
import { callsLabel, channelLabel, deviceLine, fmt, newIdempotencyKey, sla, SRC_ICON, type Column, type Lead, type LeadType } from "./inquiries";
import { clickableRow } from "../../lib/clickable";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button, { IconButton } from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card, { Badge, type BadgeTone } from "../../components/Card";
import Drawer from "../../components/Drawer";
import EmptyState from "../../components/EmptyState";
import SkeletonRows from "../../components/SkeletonRows";
import { Field, Input } from "../../components/Field";
import { tableClass, theadRowClass, Th, Td } from "../../components/Table";

const COL_ACCENT: Record<string, string> = { new: "bg-blue-500", in_progress: "bg-amber-500", won: "bg-emerald-500", lost: "bg-slate-400" };
const STATUS_LABEL: Record<string, string> = { new: "New", in_progress: "In progress", won: "Won", lost: "Lost" };
const blank = (): Lead & { sla_minutes?: number } => ({ id: "", name: "", phone: "", email: "", source: "phone", inquiry: "", sla_minutes: 60, custom_data: {} });

export function TypesManager({ types, onClose, onChanged }: { types: LeadType[]; onClose: () => void; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [mins, setMins] = useState("60");
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<any>) => { try { setErr(null); await fn(); onChanged(); } catch (e: any) { setErr(e.message); } };
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Inquiry types" className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-md p-5 space-y-4 text-xs">
        <div className="flex items-center justify-between"><h2 className="text-title font-semibold text-ink-primary">Inquiry types</h2>
          <IconButton label="Close" onClick={onClose} icon={<M n="close" c="text-base" />} /></div>
        <p className="text-ink-tertiary">Each type has its own respond-by time, for example Repair in 30 minutes or Sale in 2 hours.</p>
        <ul className="divide-y divide-slate-100 border border-slate-200 rounded-xl">
          {types.length === 0 && <li className="px-3"><EmptyState size="inline" title="No types yet." /></li>}
          {types.map((t) => (
            <li key={t.id} className={`p-3 flex items-center gap-3 ${t.active ? "" : "opacity-60"}`}>
              <span className="flex-1 font-semibold text-ink-primary">{t.name}{!t.active && " (archived)"}</span>
              <span className="text-ink-tertiary tabular-nums">{t.sla_minutes} min</span>
              {t.active
                ? <Button variant="link" className="!text-rose-600" onClick={() => act(() => api.del(`/leads/types/${t.id}`))}>Remove</Button>
                : <Button variant="link" onClick={() => act(() => api.patch(`/leads/types/${t.id}`, { active: true }))}>Restore</Button>}
            </li>
          ))}
        </ul>
        <div className="flex items-end gap-2">
          <Field className="flex-1" label="New type" htmlFor="type-name"><Input id="type-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Repair" /></Field>
          <Field className="w-36" label="Respond within (min)" htmlFor="type-mins"><Input id="type-mins" type="number" min={1} value={mins} onChange={(e) => setMins(e.target.value)} className="tabular-nums" /></Field>
          <Button variant="primary" disabled={!name.trim()} onClick={() => act(async () => { await api.post("/leads/types", { name: name.trim(), sla_minutes: Number(mins) || 60 }); setName(""); })}>Add</Button>
        </div>
        {err && <p role="alert" className="text-rose-600 font-semibold">{err}</p>}
      </div>
    </div>
  );
}

export default function InquiriesHub() {
  const navigate = useNavigate();
  const [cols, setCols] = useState<Column[] | null>(null);
  const [types, setTypes] = useState<LeadType[]>([]);
  // ?view=walkins opens the self check-in queue (the staff alert links here).
  const [view, setView] = useState<"board" | "list" | "walkins">(() => (new URLSearchParams(window.location.search).get("view") === "walkins" ? "walkins" : "board"));
  const [sel, setSel] = useState<Lead | null>(null);
  const [create, setCreate] = useState(false);
  const [showTypes, setShowTypes] = useState(false);
  const [cf, setCf] = useState(blank);
  const [idem, setIdem] = useState(newIdempotencyKey);
  const [saving, setSaving] = useState(false);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  function load() { api.get<{ columns: Column[] }>("/leads/board").then((r) => setCols(r.columns || [])).catch(() => setCols([])); }
  const loadTypes = () => api.get<{ items: LeadType[] }>("/leads/types").then((r) => setTypes(r.items || [])).catch(() => setTypes([]));
  useEffect(() => { load(); loadTypes(); }, []);
  // A missed-call alert links here with ?lead=<id>
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("lead");
    if (id) api.get<Lead>(`/leads/${id}`).then(setSel).catch(() => flash("That inquiry isn't available"));
  }, []);

  async function createLead() {
    if (!(cf.name || "").trim()) { setCreateErr("Enter the customer's name"); return; }
    setSaving(true); setCreateErr(null);
    try {
      await api.post("/leads", { name: cf.name!.trim(), phone: cf.phone || null, email: cf.email || null, source: cf.source,
        inquiry: cf.inquiry || null, sla_minutes: Number(cf.sla_minutes) || 60, lead_type_id: cf.lead_type_id || null,
        device_brand: cf.device_brand || null, device_model: cf.device_model || null, serial: cf.serial || null,
        custom_data: cf.custom_data || {}, idempotency_key: idem });
      setCreate(false); setCf(blank()); setIdem(newIdempotencyKey()); load(); flash("Inquiry logged");
    } catch (e: any) { setCreateErr(e.message); }    // keep the key: a retry can't create a second one
    finally { setSaving(false); }
  }
  const openOrder = (id: string) => navigate(`/orders?open=${encodeURIComponent(id)}`);
  const LEAD_PAGE = 50;
  const [leadPage, setLeadPage] = useState(1);
  useEffect(() => { setLeadPage(1); }, [view]);
  const allLeads = (cols || []).flatMap((c) => c.cards);
  const leadPages = Math.max(1, Math.ceil(allLeads.length / LEAD_PAGE));
  const pageLeads = allLeads.slice((Math.min(leadPage, leadPages) - 1) * LEAD_PAGE, Math.min(leadPage, leadPages) * LEAD_PAGE);

  // The empty message never repeats "New inquiry": it's already the first button in the toolbar.
  const EmptyLeads = () => (
    <EmptyState icon={<M n="mail" c="text-lg" />} title="No inquiries yet"
      body="Missed calls show up here on their own. Create a work order from one to start the job." />
  );
  const Chips = ({ l }: { l: Lead }) => {
    const s = sla(l); const calls = callsLabel(l);
    return <>
      {calls && <span className="text-2xs px-1.5 py-0.5 rounded-full font-semibold border border-rose-200 bg-rose-50 text-rose-700 flex items-center gap-0.5"><M n="phone_missed" c="text-xs" />{(l.call_count || 0) > 1 ? l.call_count : ""}</span>}
      {s && <span className={`text-2xs px-1.5 py-0.5 rounded-full font-semibold border whitespace-nowrap ${s.cls}`}>{s.label}</span>}
    </>;
  };

  const STATUS_TONE: Record<string, BadgeTone> = { won: "success", lost: "neutral", in_progress: "warning", new: "info" };

  return (
    <Shell title="Inquiries">
      {/* One toolbar for all three views; each view starts right under it at the same spot. */}
      <PageLayout mode="fill">
        <Toolbar
          actions={<>
            <Button variant="primary" onClick={() => { setCreate(true); setCreateErr(null); }} icon={<M n="add" c="text-sm" />}>New inquiry</Button>
            <Button variant="ghost" onClick={() => setShowTypes(true)} icon={<M n="label" c="text-sm" />}>Types</Button>
          </>}
          view={<Tabs look="segmented" label="Inquiry views" value={view} onChange={setView} tabs={[
            { id: "board", label: "Pipeline", icon: <M n="view_kanban" c="text-xs" /> },
            { id: "list", label: "List", icon: <M n="table_rows" c="text-xs" /> },
            { id: "walkins", label: "Walk-ins", icon: <M n="how_to_reg" c="text-xs" /> },
          ]} />}
        />

        {view === "walkins" && <WalkinsView />}

        {view === "list" && (
          <div className="flex-1 min-h-0 overflow-y-auto">
            <Card padded={false}>
              <div className="overflow-x-auto">
              <table className={tableClass}>
                <thead><tr className={theadRowClass}>
                  <Th>Inquiry</Th><Th>Channel</Th><Th>Device</Th><Th>Message</Th><Th>Status</Th><Th>Respond by</Th><Th>Result</Th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {!cols && <SkeletonRows cols={7} />}
                  {cols && allLeads.length === 0 && <tr><td colSpan={7} className="p-0"><EmptyLeads /></td></tr>}
                  {pageLeads.map((l) => (
                    <tr key={l.id} {...clickableRow(() => setSel(l))} className="hover:bg-blue-50/30 cursor-pointer">
                      <Td className="font-semibold text-ink-primary">{l.name || "Inquiry"}{l.lead_type && <span className="block text-2xs font-semibold text-ink-tertiary">{l.lead_type}</span>}</Td>
                      <Td><span className="inline-flex items-center gap-1.5"><M n={SRC_ICON[l.source || "other"] || "help"} c="text-xs text-primary" />{channelLabel(l.source)}</span></Td>
                      <Td>{deviceLine(l) || "—"}</Td>
                      <Td className="text-ink-tertiary truncate max-w-[240px]">{l.inquiry || "—"}</Td>
                      <Td><Badge tone={STATUS_TONE[l.status || "new"] || "info"}>{STATUS_LABEL[l.status || "new"] || "New"}</Badge></Td>
                      <Td><div className="flex items-center gap-1"><Chips l={l} />{!sla(l) && !callsLabel(l) && "—"}</div></Td>
                      <Td>{l.converted_order_number ? <span className="text-primary font-semibold">{l.converted_order_number}</span> : "—"}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {cols && allLeads.length > 0 && <Pager total={allLeads.length} page={Math.min(leadPage, leadPages)} pageSize={LEAD_PAGE} onPage={setLeadPage} noun="inquiries" />}
            </Card>
          </div>
        )}

        {view === "board" && <div className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden">
          <div className="flex gap-4 h-full min-w-max">
            {!cols && [0, 1, 2, 3].map((i) => (
              <div key={i} aria-hidden className="w-[80vw] sm:w-72 flex-shrink-0 space-y-2.5">
                <span className="sk block h-4 w-24 rounded" /><span className="sk block h-20 rounded-xl" /><span className="sk block h-20 rounded-xl" />
              </div>
            ))}
            {cols && allLeads.length === 0 && <div className="w-[80vw] sm:w-96"><EmptyLeads /></div>}
            {allLeads.length > 0 && cols?.map((c) => (
              <div key={c.key} className="w-[80vw] sm:w-72 flex-shrink-0 flex flex-col">
                <div className="flex items-center gap-2 px-1 pb-2">
                  <span className={`w-2 h-2 rounded-full ${COL_ACCENT[c.key] || "bg-slate-400"}`} />
                  <span className="text-xs font-semibold text-ink-primary">{c.label}</span>
                  <span className="text-2xs font-semibold tabular-nums bg-slate-100 text-ink-secondary px-1.5 py-0.5 rounded-full">{c.cards.length}</span>
                </div>
                <div className="flex-1 overflow-y-auto space-y-2.5 pr-1">
                  {c.cards.length === 0 && <EmptyState size="inline" className="px-1" title="Nothing here." />}
                  {c.cards.map((l) => (
                    <button key={l.id} onClick={() => setSel(l)} className="w-full text-left bg-white rounded-xl border border-slate-200/90 shadow-clay-sm p-3 hover:border-primary/50 transition-colors">
                      <div className="flex items-center gap-2 mb-1.5">
                        <div className="w-7 h-7 rounded-lg bg-blue-50 text-primary flex items-center justify-center flex-shrink-0"><M n={(l.call_count || 0) > 0 ? "phone_missed" : SRC_ICON[l.source || "other"] || "help"} c="text-sm" /></div>
                        <span className="text-xs font-semibold text-ink-primary truncate flex-1">{l.name || "Inquiry"}</span>
                        <Chips l={l} />
                      </div>
                      {(l.lead_type || deviceLine(l)) && <p className="text-2xs font-semibold text-ink-secondary truncate">{[l.lead_type, deviceLine(l)].filter(Boolean).join(" · ")}</p>}
                      {l.inquiry && <p className="text-2xs text-ink-secondary line-clamp-2">{l.inquiry}</p>}
                      <div className="flex items-center justify-between mt-2 text-2xs text-ink-tertiary">
                        <span>{channelLabel(l.source)}</span>
                        {l.converted_order_number ? <span className="text-primary font-semibold">{l.converted_order_number}</span> : <span>{fmt(l.created_at)}</span>}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>}
      </PageLayout>

      {sel && <InquiryDrawer lead={sel} types={types} onClose={() => setSel(null)} onOpenOrder={openOrder}
        onChanged={(l, msg) => { flash(msg); if (l) setSel(l); else setSel(null); load(); }} />}

      {create && (
        <Drawer size="sm" z={55} label="New inquiry" title="New inquiry" onClose={() => setCreate(false)}
          footer={<><Button variant="primary" onClick={createLead} busy={saving}>Log inquiry</Button><Button onClick={() => setCreate(false)}>Cancel</Button></>}>
          <InquiryFields value={cf} onChange={setCf} types={types} creating showSla />
          {createErr && <p role="alert" className="p-2.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs font-semibold">{createErr}</p>}
        </Drawer>
      )}
      {showTypes && <TypesManager types={types} onClose={() => setShowTypes(false)} onChanged={loadTypes} />}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
