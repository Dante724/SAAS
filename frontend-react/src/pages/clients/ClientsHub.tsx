import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { downloadSheet } from "../../lib/downloadSheet";
import { isoDate } from "../../lib/localDate";
import { money } from "../orders/types";
import ClientDrawer from "../orders/ClientDrawer";
import NewClientDrawer from "../../components/NewClientDrawer";
import BulkPrintDialog from "../../components/BulkPrintDialog";
import { Supplier } from "../inventory/types";
import SupplierDrawer from "./SupplierDrawer";
import { useTerminology } from "../../lib/useTerminology";
import Pager from "../../components/Pager";
import { useDebounced, useServerPage } from "../../hooks/useServerPage";
import { confirmDialog } from "../../lib/dialog";
import TagFilterChip from "./TagFilterChip";
import { useMe } from "../../context/Me";
import { Avatar } from "../orders/look";
import { MoreMenu, btnDanger, btnGhost, btnPrimary } from "../invoices/billing";
import MembershipsView from "../memberships/MembershipsView";
import { hueText, tint } from "../../lib/tint";
import ImportDrawer from "../../components/ImportDrawer";
import { clickableRow } from "../../lib/clickable";

type Client = { id: string; name?: string; phone?: string; email?: string; loyalty_tier?: string; balance?: number; devices?: any[]; order_count?: number };
type Tab = "clients" | "memberships" | "suppliers";

/* Tier is an exception worth seeing (VIP, gold, silver), so it only shows when a client has
   one; "standard" or no tier reads as a quiet dash. */
const TIER_HUE: Record<string, string> = { vip: "#7F77DD", gold: "#BA7517", silver: "#64748b" };
export function TierPill({ tier }: { tier?: string | null }) {
  const k = (tier || "").trim().toLowerCase();
  if (!k || k === "standard") return <span className="text-ink-placeholder">—</span>;
  const hue = TIER_HUE[k] || "#378ADD";
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>{k === "vip" ? "VIP" : humanize(tier)}</span>;
}

export default function ClientsHub() {
  const term = useTerminology();
  const { can } = useMe();
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(window.location.search).get("tab") === "memberships" ? "memberships" : "clients"));
  const [importing, setImporting] = useState<"clients" | "suppliers" | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [openClient, setOpenClient] = useState<string | null>(null);
  const [newClient, setNewClient] = useState(false);
  const [newSupplier, setNewSupplier] = useState(false);
  const [openSupplier, setOpenSupplier] = useState<Supplier | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [printDlg, setPrintDlg] = useState(false);
  const [printIds, setPrintIds] = useState<string[] | null>(null);   // one row's "Print label", else the selection
  const [mergePrev, setMergePrev] = useState<any>(null);
  const [merged, setMerged] = useState<{ logId: string; targetName: string; count: number } | null>(null);

  // Clients page on the server (search included); suppliers are few and stay client-side.
  const dq = useDebounced(q.trim());
  const [tagId, setTagId] = useState<string | null>(null);
  const cp = useServerPage<Client>("/clients", { search: dq || undefined, tag: tagId || undefined }, { pageSize: 50 });
  const rows = cp.rows;
  useEffect(() => { setErr(cp.err); }, [cp.err]);   // clears once a later page or filter loads
  function loadClients() { cp.reload(); }
  function loadSuppliers() {
    api.get<{ items: Supplier[] }>("/suppliers").then((r) => setSuppliers(r.items || [])).catch(() => setSuppliers([]));
  }
  useEffect(() => { if (tab === "suppliers" && !suppliers) loadSuppliers(); }, [tab]);

  const cRows = rows ?? [];
  const sRows = (suppliers ?? []).filter((s) => !q || [s.name, s.contact_name, s.phone, s.email].some((v) => (v || "").toLowerCase().includes(q.toLowerCase())));
  // Numbered pagination — one shared page (Clients and Suppliers tabs never show at once).
  const PAGE_SIZE = 50;
  const [listPage, setListPage] = useState(1);
  useEffect(() => { setListPage(1); }, [tab, q]);
  const pageC = cRows;
  const sPages = Math.max(1, Math.ceil(sRows.length / PAGE_SIZE));
  const pageS = sRows.slice((Math.min(listPage, sPages) - 1) * PAGE_SIZE, Math.min(listPage, sPages) * PAGE_SIZE);

  const allSel = cRows.length > 0 && cRows.every((c) => sel.has(c.id));
  function toggle(id: string) { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); setSel(n); }
  function toggleAll() { const n = new Set(sel); cRows.forEach((c) => (allSel ? n.delete(c.id) : n.add(c.id))); setSel(n); }
  function clearSel() { setSel(new Set()); }

  async function bulkDelete() {
    if (!(await confirmDialog(`Delete ${sel.size} contact(s)? This cannot be undone.`, { danger: true, confirmLabel: "Delete" }))) return;
    setBulkBusy(true);
    try { for (const id of sel) await api.del(`/clients/${id}`); clearSel(); loadClients(); }
    catch (e: any) { setErr(e.message || "Bulk delete failed"); }
    finally { setBulkBusy(false); }
  }
  async function deleteOne(c: Client) {
    if (!(await confirmDialog(`Delete ${c.name || "this contact"}? This cannot be undone.`, { danger: true, confirmLabel: "Delete" }))) return;
    try { await api.del(`/clients/${c.id}`); const n = new Set(sel); n.delete(c.id); setSel(n); loadClients(); }
    catch (e: any) { setErr(e.message || "Delete failed"); }
  }
  async function bulkMerge() {
    const ids = [...sel];
    if (ids.length < 2) return;
    setBulkBusy(true);
    try { const p = await api.post<any>("/clients/merge/preview", { target_id: ids[0], source_ids: ids.slice(1) }); setMergePrev(p); }
    catch (e: any) { setErr(e.message || "Could not preview merge"); }
    finally { setBulkBusy(false); }
  }
  async function confirmMerge() {
    if (!mergePrev) return;
    setBulkBusy(true);
    try {
      const res = await api.post<any>("/clients/merge", { target_id: mergePrev.target.id, source_ids: mergePrev.sources.map((s: any) => s.id) });
      setMerged({ logId: res.merge_log_id, targetName: mergePrev.target.name, count: mergePrev.sources.length });
      setMergePrev(null); clearSel(); loadClients();
    } catch (e: any) { setErr(e.message || "Merge failed"); }
    finally { setBulkBusy(false); }
  }
  async function undoMerge() {
    if (!merged) return;
    setBulkBusy(true);
    try { await api.post(`/clients/merge/${merged.logId}/undo`, {}); setMerged(null); loadClients(); }
    catch (e: any) { setErr(e.message || "Undo failed"); }
    finally { setBulkBusy(false); }
  }
  function bulkExport() {
    const chosen = [...sel].map((id) => cp.seen.get(id)).filter(Boolean) as Client[];
    const head = ["Name", "Phone", "Email", "Tier", "Balance", "Orders"];
    const body = chosen.map((c) => [c.name ?? "", c.phone ?? "", c.email ?? "", c.loyalty_tier || "Standard", c.balance ?? 0, c.order_count ?? 0]);
    downloadSheet([head, ...body], "csv", `contacts-${isoDate()}`);
  }

  return (
    <Shell title="Contacts">
      {importing && <ImportDrawer kind={importing} onClose={() => setImporting(null)} onDone={() => { if (importing === "clients") loadClients(); else loadSuppliers(); }} />}
      <div className="flex-1 overflow-y-auto">
        <div className="px-6 pt-4 flex items-center gap-1 border-b border-slate-200/70">
          {(["clients", "memberships", "suppliers"] as Tab[]).map((t) => (
            <button key={t} onClick={() => { setTab(t); clearSel(); }}
              className={`px-3 py-2 text-sm font-semibold rounded-t-lg -mb-px border-b-2 capitalize ${tab === t ? "text-primary border-primary" : "text-ink-tertiary border-transparent hover:text-ink-primary"}`}>
              {t}
            </button>
          ))}
        </div>

        <div className="p-6 space-y-4">
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3.5 flex flex-wrap items-center justify-between gap-3">
            {tab === "clients"
              ? <div className="flex items-center gap-2">
                  {can("can_create_clients") && <button onClick={() => setNewClient(true)} className={btnPrimary}><M n="add" c="text-sm" />New client</button>}
                  {can("can_create_clients") && <button onClick={() => setImporting("clients")} className={`${btnGhost} bg-white`}><M n="upload" c="text-sm" />Import</button>}
                </div>
              : tab === "suppliers" ? <div className="flex items-center gap-2">
                  <button onClick={() => setNewSupplier(true)} className={btnPrimary}><M n="add" c="text-sm" />New supplier</button>
                  {can("can_manage_inventory") && <button onClick={() => setImporting("suppliers")} className={`${btnGhost} bg-white`}><M n="upload" c="text-sm" />Import</button>}
                </div>
              : <a href="/catalog?tab=plans" className={`${btnGhost} bg-white`}><M n="workspace_premium" c="text-sm" />Manage plans</a>}
            <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto">
              {tab === "clients" && <TagFilterChip value={tagId} onChange={(id) => { setTagId(id); clearSel(); }} />}
              <div className="relative w-full sm:w-72"><M n="search" c="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" /><input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search contacts" className="glass-input w-full h-9 rounded-xl pl-8 pr-3 py-2 text-sm text-ink-primary" placeholder="Search name, phone or email…" /></div>
            </div>
          </div>

          {tab === "memberships" && <MembershipsView q={dq} />}

          {tab === "clients" && merged && (
            <div className="bg-emerald-50 rounded-2xl border border-emerald-200 shadow-clay-sm px-4 py-2.5 flex items-center gap-2 text-xs font-semibold">
              <M n="check_circle" c="text-sm text-emerald-600" />
              <span className="text-emerald-800">Merged {merged.count} contact{merged.count === 1 ? "" : "s"} into {merged.targetName}.</span>
              <div className="flex-1" />
              <button onClick={undoMerge} disabled={bulkBusy} className="px-3 py-1.5 rounded-lg bg-white border border-emerald-300 text-emerald-700 hover:bg-emerald-50 disabled:opacity-50 flex items-center gap-1"><M n="history" c="text-sm" />Undo</button>
              <button aria-label="Close" onClick={() => setMerged(null)} className="px-2 py-1.5 rounded-lg hover:bg-emerald-100 text-emerald-700"><M n="close" c="text-sm" /></button>
            </div>
          )}

          {tab === "clients" && sel.size > 0 && (
            <div className="bg-primary/5 rounded-2xl border border-primary/30 shadow-clay-sm px-4 py-2.5 flex flex-wrap items-center gap-2 text-xs font-semibold">
              <span className="text-primary mr-1">{sel.size} selected</span>
              <button onClick={() => { setPrintIds(null); setPrintDlg(true); }} disabled={bulkBusy} className={btnGhost}><M n="print" c="text-sm" />Print</button>
              <button onClick={bulkExport} disabled={bulkBusy} className={btnGhost}><M n="download" c="text-sm" />Export CSV</button>
              <button onClick={bulkMerge} disabled={bulkBusy || sel.size < 2} className={btnGhost}><M n="merge" c="text-sm" />Merge</button>
              <button onClick={bulkDelete} disabled={bulkBusy} className={btnDanger}><M n="delete" c="text-sm" />Delete</button>
              <button aria-label="Close" onClick={clearSel} className="ml-auto h-9 w-9 grid place-items-center rounded-xl hover:bg-slate-200/60 text-ink-tertiary"><M n="close" c="text-sm" /></button>
            </div>
          )}

          {tab === "clients" && (
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
              <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-xs font-semibold">
                  <th className="py-3 pl-4 pr-1 w-8"><input type="checkbox" aria-label="Select all" checked={allSel} onChange={toggleAll} className="accent-primary cursor-pointer" /></th>
                  <th className="py-3 px-4">Client</th><th className="py-3 px-4">Phone</th><th className="py-3 px-4">Email</th>
                  <th className="py-3 px-4">Tier</th><th className="py-3 px-4 text-right">Balance</th><th className="py-3 px-4">{term.assets}</th><th className="py-3 px-4">Orders</th><th className="py-3 pl-2 pr-3 w-12"><span className="sr-only">Actions</span></th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {err && <tr><td colSpan={9} className="py-6 px-4 text-status-overdue-ink">Error: {err}</td></tr>}
                  {!rows && !err && <SkeletonRows cols={9} />}
                  {rows && !err && cRows.length === 0 && (
                    <tr><td colSpan={9} className="py-10 px-4 text-center">
                      {tagId || dq ? <p className="text-ink-tertiary">{tagId ? "No clients with this tag." : "No clients found."}</p> : (
                        <div className="space-y-3">
                          <p className="text-ink-secondary">No clients yet. Add one or import a CSV to start.</p>
                          {can("can_create_clients") && <button onClick={() => setNewClient(true)} className={btnPrimary}><M n="add" c="text-sm" />New client</button>}
                        </div>
                      )}
                    </td></tr>
                  )}
                  {pageC.map((c) => (
                    <tr key={c.id} className={`hover:bg-slate-50 cursor-pointer ${sel.has(c.id) ? "bg-primary/5" : ""}`} {...clickableRow(() => setOpenClient(c.id))}>
                      <td className="py-3 pl-4 pr-1" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select row" checked={sel.has(c.id)} onChange={() => toggle(c.id)} className="accent-primary cursor-pointer" /></td>
                      <td className="py-2.5 px-4"><span className="flex items-center gap-2.5 min-w-0"><Avatar name={/\p{L}/u.test(c.name || "") ? c.name : null} size={26} /><span className="font-semibold text-ink-primary truncate">{c.name || "—"}</span></span></td>
                      <td className="py-3 px-4 tabular-nums whitespace-nowrap">{c.phone || "—"}</td>
                      <td className="py-3 px-4 text-ink-tertiary">{c.email || "—"}</td>
                      <td className="py-3 px-4"><TierPill tier={c.loyalty_tier} /></td>
                      <td className="py-3 px-4 text-right tabular-nums font-semibold text-ink-primary">{money(c.balance ?? 0)}</td>
                      <td className="py-3 px-4 tabular-nums font-semibold text-ink-primary">{c.devices?.length ?? 0}</td>
                      <td className="py-3 px-4 tabular-nums font-semibold text-ink-primary">{c.order_count ?? 0}</td>
                      <td className="py-1.5 pl-2 pr-3" onClick={(e) => e.stopPropagation()}>
                        <MoreMenu label={`More actions for ${c.name || "client"}`} items={[
                          { label: "Open", icon: "open_in_new", onClick: () => setOpenClient(c.id) },
                          { label: "Print label", icon: "print", onClick: () => { setPrintIds([c.id]); setPrintDlg(true); } },
                          { label: "Delete", icon: "delete", danger: true, onClick: () => deleteOne(c) },
                        ]} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {rows && cRows.length > 0 && <Pager total={cp.total} page={cp.page} pageSize={cp.pageSize} onPage={cp.setPage} noun="clients" />}
            </div>
          )}

          {tab === "suppliers" && (
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
              <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-xs font-semibold">
                  <th className="py-3 px-4">Supplier</th><th className="py-3 px-4">Contact</th><th className="py-3 px-4">Phone</th><th className="py-3 px-4">Email</th>
                  <th className="py-3 px-4 text-right">Lead days</th><th className="py-3 px-4 text-right">Balance</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {!suppliers && <SkeletonRows cols={6} />}
                  {suppliers && sRows.length === 0 && <tr><td colSpan={6} className="py-10 px-4 text-center">
                    {q ? <p className="text-ink-tertiary">No suppliers found.</p> : (
                      <div className="space-y-3">
                        <p className="text-ink-secondary">No suppliers yet.</p>
                        <button onClick={() => setNewSupplier(true)} className={btnPrimary}><M n="add" c="text-sm" />New supplier</button>
                      </div>
                    )}
                  </td></tr>}
                  {pageS.map((s) => (
                    <tr key={s.id} {...clickableRow(() => s.client_id ? setOpenClient(s.client_id) : setOpenSupplier(s))} className="hover:bg-slate-50 cursor-pointer">
                      <td className="py-3 px-4 font-semibold text-ink-primary">{s.name || "—"}</td>
                      <td className="py-3 px-4">{s.contact_name || "—"}</td>
                      <td className="py-3 px-4 tabular-nums">{s.phone || "—"}</td>
                      <td className="py-3 px-4 text-ink-tertiary">{s.email || "—"}</td>
                      <td className="py-3 px-4 text-right tabular-nums">{s.lead_days ?? "—"}</td>
                      <td className="py-3 px-4 text-right tabular-nums font-semibold text-ink-primary">{money(s.balance ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {suppliers && sRows.length > 0 && <Pager total={sRows.length} page={Math.min(listPage, sPages)} pageSize={PAGE_SIZE} onPage={setListPage} noun="suppliers" />}
            </div>
          )}
        </div>
      </div>

      {openClient && <ClientDrawer clientId={openClient} onClose={() => { setOpenClient(null); loadClients(); if (tab === "suppliers") loadSuppliers(); }} onOpenAsset={() => {}} />}
      {newClient && <NewClientDrawer onClose={() => setNewClient(false)} onCreated={() => { setNewClient(false); loadClients(); }} />}
      {newSupplier && <NewSupplierDrawer onClose={() => setNewSupplier(false)} onCreated={() => { setNewSupplier(false); loadSuppliers(); }} />}
      {openSupplier && <SupplierDrawer supplier={openSupplier} onClose={() => setOpenSupplier(null)} onSaved={loadSuppliers} />}
      <BulkPrintDialog open={printDlg} onClose={() => { setPrintDlg(false); setPrintIds(null); }} ids={printIds ?? [...sel]} noun="client"
        endpoint="/documents/render-client-labels" idKey="client_ids"
        docTypes={["client_label", "label", "contact_card"]} />
      {mergePrev && <MergePreviewDialog preview={mergePrev} busy={bulkBusy} onCancel={() => setMergePrev(null)} onConfirm={confirmMerge} />}
    </Shell>
  );
}

/* ── Merge preview dialog — shows what a merge will move before it runs ───────── */
function MergePreviewDialog({ preview, busy, onCancel, onConfirm }: { preview: any; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const p = preview;
  const moveRows = (Object.entries(p.moved as Record<string, number>) as [string, number][]).filter(([, n]) => n > 0);
  const totalMoved = moveRows.reduce((s, [, n]) => s + n, 0);
  const LABELS: Record<string, string> = { devices: "devices / assets", orders: "work orders", invoices: "invoices", payments: "payments", feedback: "feedback", ledger: "balance entries" };
  const FLD: Record<string, string> = { phone: "Phone", email: "Email", address: "Address", notes: "Notes", discount_card: "Discount card", source: "Lead source", manager_id: "Manager" };
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" aria-hidden="true" onClick={busy ? undefined : onCancel} />
      <div className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-md max-h-[85vh] overflow-y-auto">
        <div className="p-5 border-b border-slate-200 flex items-center gap-2">
          <span className="w-9 h-9 rounded-xl bg-amber-50 text-amber-600 grid place-items-center border border-amber-100"><M n="merge" c="text-base" /></span>
          <div><h3 className="text-sm font-bold text-ink-primary">Merge {p.sources.length} contact{p.sources.length === 1 ? "" : "s"}</h3>
            <p className="text-[11px] text-ink-tertiary">Everything below moves into <span className="font-bold text-ink-primary">{p.target.name}</span>.</p></div>
        </div>
        <div className="p-5 space-y-4 text-xs">
          <div>
            <div className="text-[11px] font-bold text-ink-tertiary mb-1.5">Merging in</div>
            {p.sources.map((s: any) => <div key={s.id} className="flex items-center justify-between py-0.5"><span className="font-semibold text-ink-primary">{s.name}</span><span className="text-ink-tertiary">→ removed</span></div>)}
          </div>
          <div>
            <div className="text-[11px] font-bold text-ink-tertiary mb-1.5">What moves {totalMoved ? `(${totalMoved})` : ""}</div>
            {moveRows.length === 0 ? <div className="text-ink-tertiary">No linked records — just the contact details.</div> :
              <div className="grid grid-cols-2 gap-1.5">{moveRows.map(([k, n]) => <div key={k} className="flex items-center gap-1.5"><span className="tabular-nums font-bold text-ink-primary">{n}</span><span className="text-ink-secondary">{LABELS[k] || k}</span></div>)}</div>}
          </div>
          {p.field_fills.length > 0 && (
            <div>
              <div className="text-[11px] font-bold text-ink-tertiary mb-1.5">Blank fields filled</div>
              {p.field_fills.map((f: any, i: number) => <div key={i} className="flex items-center justify-between py-0.5 gap-2"><span className="text-ink-secondary whitespace-nowrap">{FLD[f.field] || f.field}</span><span className="font-semibold text-ink-primary truncate" title={String(f.value)}>{String(f.value)}</span></div>)}
            </div>
          )}
          <div className="flex items-center justify-between pt-1 border-t border-slate-100">
            <span className="text-[11px] font-bold text-ink-tertiary">Combined balance</span>
            <span className={`tabular-nums font-bold ${(p.result_balance ?? 0) > 0 ? "text-rose-600" : "text-emerald-600"}`}>{Math.abs(p.result_balance ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {(p.result_balance ?? 0) > 0 ? "(owes)" : "(credit)"}</span>
          </div>
          <div className="flex items-start gap-1.5 p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-[11px] text-ink-secondary"><M n="history" c="text-sm mt-0.5" /><span>The source contacts are removed, but you can undo this right after if it's wrong.</span></div>
        </div>
        <div className="p-4 border-t border-slate-200 flex justify-end gap-2">
          <button onClick={onCancel} disabled={busy} className="px-4 py-2 rounded-xl text-xs font-bold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50 disabled:opacity-50">Cancel</button>
          <button onClick={onConfirm} disabled={busy} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-50">{busy ? "Merging…" : "Merge contacts"}</button>
        </div>
      </div>
    </div>
  );
}

/* ── New supplier drawer (create; POST /suppliers) ─────────────── */
function NewSupplierDrawer({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [f, setF] = useState<any>({ name: "", contact_name: "", phone: "", email: "", lead_days: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  async function save() {
    if (!f.name.trim()) { setErr("Supplier name is required"); return; }
    setBusy(true); setErr(null);
    try {
      await api.post("/suppliers", {
        name: f.name.trim(), contact_name: f.contact_name || null, phone: f.phone || null,
        email: f.email || null, lead_days: f.lead_days ? Number(f.lead_days) : null,
      });
      onCreated();
    } catch (e: any) { setErr(e.message || "Could not create supplier"); setBusy(false); }
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" aria-hidden="true" onClick={onClose} />
      <div className="relative w-[440px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary">New supplier</h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          {[["name", "Supplier name *"], ["contact_name", "Contact person"], ["phone", "Phone"], ["email", "Email"], ["lead_days", "Lead time (days)"]].map(([k, l]) => (
            <div key={k}>
              <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">{l}</label>
              <input value={f[k]} onChange={set(k)} type={k === "lead_days" ? "number" : "text"} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary" />
            </div>
          ))}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Create supplier"}</button>
        </div>
      </div>
    </div>
  );
}
