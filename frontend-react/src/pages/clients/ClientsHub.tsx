import SkeletonRows from "../../components/SkeletonRows";
import { useMe, hasFeature } from "../../context/Me";
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
import { Avatar } from "../orders/look";
import { MoreMenu } from "../invoices/billing";
import MembershipsView from "../memberships/MembershipsView";
import { hueText, tint } from "../../lib/tint";
import { clickableRow } from "../../lib/clickable";
import ImportDrawer from "../../components/ImportDrawer";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button, { IconButton, buttonClass } from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card from "../../components/Card";
import Drawer from "../../components/Drawer";
import { ErrorState, TableEmpty } from "../../components/EmptyState";
import { Field, Input } from "../../components/Field";
import { tableClass, theadRowClass, Th, Td } from "../../components/Table";
import SearchBox from "../orders/SearchBox";

type Client = { id: string; name?: string; phone?: string; email?: string; loyalty_tier?: string; balance?: number; devices?: any[]; order_count?: number };
type Tab = "clients" | "memberships" | "suppliers";

/* Tier is an exception worth seeing (VIP, gold, silver), so it only shows when a client has
   one; "standard" or no tier reads as a quiet dash. */
const TIER_HUE: Record<string, string> = { vip: "#7F77DD", gold: "#BA7517", silver: "#64748b" };
export function TierPill({ tier }: { tier?: string | null }) {
  const k = (tier || "").trim().toLowerCase();
  if (!k || k === "standard") return <span className="text-ink-placeholder">—</span>;
  const hue = TIER_HUE[k] || "#378ADD";
  return <span className="inline-flex items-center h-6 px-2 rounded-full text-2xs font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>{k === "vip" ? "VIP" : humanize(tier)}</span>;
}

export default function ClientsHub() {
  const term = useTerminology();
  const { can, me } = useMe();
  const tabs: Tab[] = hasFeature(me, "inventory") ? ["clients", "memberships", "suppliers"] : ["clients", "memberships"];   // suppliers: Shop plan
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

  const TAB_LABEL: Record<Tab, string> = { clients: "Clients", memberships: "Memberships", suppliers: "Suppliers" };
  const canImport = tab === "clients" ? can("can_create_clients") : tab === "suppliers" ? can("can_manage_inventory") : false;
  // The main button keeps one width on every tab, so Import beside it never moves.
  const mainW = "min-w-[8.75rem]";

  return (
    <Shell title="Contacts">
      {importing && <ImportDrawer kind={importing} onClose={() => setImporting(null)} onDone={() => { if (importing === "clients") loadClients(); else loadSuppliers(); }} />}
      <div className="px-6 pt-5 flex-shrink-0">
        <Tabs label="Contacts sections" value={tab} onChange={(t) => { setTab(t); clearSel(); }} tabs={tabs.map((t) => ({ id: t, label: TAB_LABEL[t] }))} />
      </div>
      <PageLayout>
        {/* One toolbar for every tab: the main action first (its label follows the tab), Import next
            (greyed where it doesn't apply), the tag filter (clients only, greyed elsewhere) and search. */}
        <Toolbar
          actions={<>
            {tab === "clients" && <Button variant="primary" className={mainW} disabled={!can("can_create_clients")} onClick={() => setNewClient(true)} icon={<M n="add" c="text-sm" />}>New client</Button>}
            {tab === "suppliers" && <Button variant="primary" className={mainW} onClick={() => setNewSupplier(true)} icon={<M n="add" c="text-sm" />}>New supplier</Button>}
            {tab === "memberships" && <a href="/catalog?tab=plans" className={buttonClass("primary", "md", mainW)}><M n="workspace_premium" c="text-sm" />Manage plans</a>}
            {(can("can_create_clients") || can("can_manage_inventory")) && <Button disabled={!canImport} onClick={() => setImporting(tab === "suppliers" ? "suppliers" : "clients")} icon={<M n="upload" c="text-sm" />}>Import</Button>}
          </>}
          filters={<TagFilterChip value={tagId} disabled={tab !== "clients"} onChange={(id) => { setTagId(id); clearSel(); }} />}
          search={<SearchBox value={q} onChange={setQ} label="Search contacts" placeholder="Search name, phone or email…" />}
        />

          {tab === "memberships" && <MembershipsView q={dq} />}

          {tab === "clients" && merged && (
            <div role="status" className="bg-emerald-50 rounded-2xl border border-emerald-200 px-4 py-2.5 flex items-center gap-2 text-xs font-semibold">
              <M n="check_circle" c="text-sm text-emerald-600" />
              <span className="text-emerald-800">Merged {merged.count} contact{merged.count === 1 ? "" : "s"} into {merged.targetName}.</span>
              <div className="flex-1" />
              <Button size="sm" onClick={undoMerge} disabled={bulkBusy} icon={<M n="history" c="text-sm" />}>Undo</Button>
              <IconButton size="sm" label="Close" onClick={() => setMerged(null)} icon={<M n="close" c="text-sm" />} />
            </div>
          )}

          {tab === "clients" && sel.size > 0 && (
            <div className="bg-primary/5 rounded-2xl border border-primary/30 px-4 py-2.5 flex flex-wrap items-center gap-2 text-xs font-semibold">
              <span className="text-primary mr-1 tabular-nums">{sel.size} selected</span>
              <Button onClick={() => { setPrintIds(null); setPrintDlg(true); }} disabled={bulkBusy} icon={<M n="print" c="text-sm" />}>Print</Button>
              <Button onClick={bulkExport} disabled={bulkBusy} icon={<M n="download" c="text-sm" />}>Export CSV</Button>
              <Button onClick={bulkMerge} disabled={bulkBusy || sel.size < 2} icon={<M n="merge" c="text-sm" />}>Merge</Button>
              <Button variant="danger" onClick={bulkDelete} disabled={bulkBusy} icon={<M n="delete" c="text-sm" />}>Delete</Button>
              <IconButton label="Close" className="ml-auto" onClick={clearSel} icon={<M n="close" c="text-sm" />} />
            </div>
          )}

          {tab === "clients" && (
            <Card padded={false}>
              <div className="overflow-x-auto">
              <table className={`${tableClass} min-w-[860px]`}>
                <thead><tr className={theadRowClass}>
                  <Th className="!pl-4 !pr-1 w-8"><input type="checkbox" aria-label="Select all" checked={allSel} onChange={toggleAll} className="w-4 h-4 accent-primary cursor-pointer" /></Th>
                  <Th>Client</Th><Th>Phone</Th><Th>Email</Th>
                  <Th>Tier</Th><Th num>Balance</Th><Th num>{term.assets}</Th><Th num>Orders</Th><Th className="!pl-2 !pr-3 w-12"><span className="sr-only">Actions</span></Th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {err && <tr><td colSpan={9} className="p-0"><ErrorState body={err} onRetry={loadClients} /></td></tr>}
                  {!rows && !err && <SkeletonRows cols={9} />}
                  {rows && !err && cRows.length === 0 && (tagId || dq
                    ? <TableEmpty cols={9} icon={<M n="search" c="text-lg" />} title={tagId ? "No clients with this tag" : "No clients found"} body={tagId ? "Clear the tag to see everyone." : "Try a different name, phone or email."} />
                    : <TableEmpty cols={9} icon={<M n="group" c="text-lg" />} title="No clients yet" body="Add one or import a CSV to start." />)}
                  {pageC.map((c) => (
                    <tr key={c.id} className={`hover:bg-slate-50 cursor-pointer ${sel.has(c.id) ? "bg-primary/5" : ""}`} {...clickableRow(() => setOpenClient(c.id))}>
                      <td className="py-3 pl-4 pr-1" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select row" checked={sel.has(c.id)} onChange={() => toggle(c.id)} className="w-4 h-4 accent-primary cursor-pointer" /></td>
                      <td className="py-2.5 px-4"><span className="flex items-center gap-2.5 min-w-0"><Avatar name={/\p{L}/u.test(c.name || "") ? c.name : null} size={26} /><span className="font-semibold text-ink-primary truncate">{c.name || "—"}</span></span></td>
                      <Td className="tabular-nums whitespace-nowrap">{c.phone || "—"}</Td>
                      <Td className="text-ink-tertiary">{c.email || "—"}</Td>
                      <Td><TierPill tier={c.loyalty_tier} /></Td>
                      <Td num className="font-semibold text-ink-primary">{money(c.balance ?? 0)}</Td>
                      <Td num className="font-semibold text-ink-primary">{c.devices?.length ?? 0}</Td>
                      <Td num className="font-semibold text-ink-primary">{c.order_count ?? 0}</Td>
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
            </Card>
          )}

          {tab === "suppliers" && (
            <Card padded={false}>
              <div className="overflow-x-auto">
              <table className={`${tableClass} min-w-[720px]`}>
                <thead><tr className={theadRowClass}>
                  <Th>Supplier</Th><Th>Contact</Th><Th>Phone</Th><Th>Email</Th>
                  <Th num>Lead days</Th><Th num>Balance</Th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {!suppliers && <SkeletonRows cols={6} />}
                  {suppliers && sRows.length === 0 && (q
                    ? <TableEmpty cols={6} icon={<M n="search" c="text-lg" />} title="No suppliers found" body="Try a different name, phone or email." />
                    : <TableEmpty cols={6} icon={<M n="local_shipping" c="text-lg" />} title="No suppliers yet" body="Suppliers you buy parts from show up here." />)}
                  {pageS.map((s) => (
                    <tr key={s.id} {...clickableRow(() => s.client_id ? setOpenClient(s.client_id) : setOpenSupplier(s))} className="hover:bg-slate-50 cursor-pointer">
                      <Td className="font-semibold text-ink-primary">{s.name || "—"}</Td>
                      <Td>{s.contact_name || "—"}</Td>
                      <Td className="tabular-nums">{s.phone || "—"}</Td>
                      <Td className="text-ink-tertiary">{s.email || "—"}</Td>
                      <Td num>{s.lead_days ?? "—"}</Td>
                      <Td num className="font-semibold text-ink-primary">{money(s.balance ?? 0)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {suppliers && sRows.length > 0 && <Pager total={sRows.length} page={Math.min(listPage, sPages)} pageSize={PAGE_SIZE} onPage={setListPage} noun="suppliers" />}
            </Card>
          )}
      </PageLayout>

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
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={busy ? undefined : onCancel} />
      <div role="dialog" aria-modal="true" aria-label="Merge contacts" className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-md max-h-[85vh] overflow-y-auto">
        <div className="p-5 border-b border-slate-200 flex items-center gap-2">
          <span className="w-9 h-9 rounded-xl bg-amber-50 text-amber-600 grid place-items-center border border-amber-100"><M n="merge" c="text-base" /></span>
          <div><h2 className="text-title font-semibold text-ink-primary">Merge {p.sources.length} contact{p.sources.length === 1 ? "" : "s"}</h2>
            <p className="text-2xs text-ink-tertiary">Everything below moves into <span className="font-semibold text-ink-primary">{p.target.name}</span>.</p></div>
        </div>
        <div className="p-5 space-y-4 text-xs">
          <div>
            <div className="text-2xs font-semibold text-ink-tertiary mb-1.5">Merging in</div>
            {p.sources.map((s: any) => <div key={s.id} className="flex items-center justify-between py-0.5"><span className="font-semibold text-ink-primary">{s.name}</span><span className="text-ink-tertiary">→ removed</span></div>)}
          </div>
          <div>
            <div className="text-2xs font-semibold text-ink-tertiary mb-1.5">What moves {totalMoved ? `(${totalMoved})` : ""}</div>
            {moveRows.length === 0 ? <div className="text-ink-tertiary">No linked records — just the contact details.</div> :
              <div className="grid grid-cols-2 gap-1.5">{moveRows.map(([k, n]) => <div key={k} className="flex items-center gap-1.5"><span className="tabular-nums font-semibold text-ink-primary">{n}</span><span className="text-ink-secondary">{LABELS[k] || k}</span></div>)}</div>}
          </div>
          {p.field_fills.length > 0 && (
            <div>
              <div className="text-2xs font-semibold text-ink-tertiary mb-1.5">Blank fields filled</div>
              {p.field_fills.map((f: any, i: number) => <div key={i} className="flex items-center justify-between py-0.5 gap-2"><span className="text-ink-secondary whitespace-nowrap">{FLD[f.field] || f.field}</span><span className="font-semibold text-ink-primary truncate" title={String(f.value)}>{String(f.value)}</span></div>)}
            </div>
          )}
          <div className="flex items-center justify-between pt-1 border-t border-slate-100">
            <span className="text-2xs font-semibold text-ink-tertiary">Combined balance</span>
            <span className={`tabular-nums font-semibold ${(p.result_balance ?? 0) > 0 ? "text-rose-600" : "text-emerald-600"}`}>{Math.abs(p.result_balance ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {(p.result_balance ?? 0) > 0 ? "(owes)" : "(credit)"}</span>
          </div>
          <div className="flex items-start gap-1.5 p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-2xs text-ink-secondary"><M n="history" c="text-sm mt-0.5" /><span>The source contacts are removed, but you can undo this right after if it's wrong.</span></div>
        </div>
        <div className="p-4 border-t border-slate-200 flex flex-wrap gap-2">
          <Button variant="primary" onClick={onConfirm} busy={busy}>{busy ? "Merging…" : "Merge contacts"}</Button>
          <Button onClick={onCancel} disabled={busy}>Cancel</Button>
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
    <Drawer size="sm" label="New supplier" title="New supplier" onClose={onClose}
      footer={<><Button variant="primary" onClick={save} busy={busy}>{busy ? "Saving…" : "Create supplier"}</Button><Button onClick={onClose}>Cancel</Button></>}>
      {err && <div role="alert" className="rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      {[["name", "Supplier name *"], ["contact_name", "Contact person"], ["phone", "Phone"], ["email", "Email"], ["lead_days", "Lead time (days)"]].map(([k, l]) => (
        <Field key={k} label={l} htmlFor={`ns-${k}`}>
          <Input id={`ns-${k}`} value={f[k]} onChange={set(k)} type={k === "lead_days" ? "number" : "text"} />
        </Field>
      ))}
    </Drawer>
  );
}
