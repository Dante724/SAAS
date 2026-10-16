import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api, openAuthedDoc } from "../../api/client";
import DateField from "../../components/DateField";
import { Product, money } from "./types";
import { confirmDialog } from "../../lib/dialog";

/** PO detail + the missing Receive action. Receiving posts to the ONE real path
 * (`/inventory/po/{id}/receive`) that lands stock, moving-average cost, FIFO layers,
 * landed cost, supplier balance and honours reservations. Before this the whole
 * pipeline was unreachable and POs stayed open forever. */
type Line = { id: string; part_id: string; part?: string; qty_ordered: number; qty_received: number; unit_cost: number };
type Charge = { label: string; amount: number };
type Match = {
  status: "pending" | "unbilled" | "matched" | "variance";
  ordered_qty: number; received_qty: number; qty_variance: number;
  expected_cost: number; invoiced_total?: number | null; price_variance?: number | null;
};
type WfStatus = { key: string; name: string; color: string; status_group: string };
type PODetail = {
  id: string; po_number?: string; status?: string; workflow_status?: string | null; on_credit?: boolean;
  supplier_invoice_no?: string | null; supplier_invoice_date?: string | null; notes?: string | null;
  supplier_invoice_total?: number | null; match?: Match;
  supplier_invoice_file?: { id: string; url: string; name?: string } | null;
  charges?: Charge[]; charges_to_cost?: boolean; alloc_basis?: string;
  shipment_freight?: number; freight_capitalised?: number;
  expected_at?: string | null; ordered_at?: string | null; received_at?: string | null;
  subtotal?: number; charges_total?: number; total?: number; editable?: boolean;
  lines: Line[]; supplier?: { id: string; name?: string; balance?: number };
};

const d = (s?: string | null) => (s ? new Date(s).toLocaleDateString() : "—");

export default function PODrawer({ poId, onClose, onChanged }: { poId: string; onClose: () => void; onChanged: () => void }) {
  const [po, setPo] = useState<PODetail | null>(null);
  const [recv, setRecv] = useState<Record<string, number>>({});
  const [inv, setInv] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  // edit mode + send
  const [editing, setEditing] = useState(false);
  const [ed, setEd] = useState<any>(null);           // editable working copy
  const [pool, setPool] = useState<Product[]>([]);
  const [sendMenu, setSendMenu] = useState(false);
  const [statusMenu, setStatusMenu] = useState(false);
  const [pipeline, setPipeline] = useState<WfStatus[]>([]);   // configurable PO workflow stages
  const [receipts, setReceipts] = useState<{ id: string; part: string; qty: number; unit_cost: number; by?: string | null; at: string }[]>([]);
  const [scan, setScan] = useState("");

  function load() {
    api.get<PODetail>(`/inventory/po/${poId}`).then((p) => {
      setPo(p);
      setInv(p.supplier_invoice_total != null ? String(p.supplier_invoice_total) : "");
      // default each line's receive qty to the outstanding remainder
      setRecv(Object.fromEntries(p.lines.map((l) => [l.id, Math.max((l.qty_ordered || 0) - (l.qty_received || 0), 0)])));
    }).catch(() => setToast("Could not load PO"));
    api.get<any>(`/inventory/po/${poId}/receipts`).then((r) => setReceipts(r.items || [])).catch(() => {});
  }
  useEffect(load, [poId]);
  useEffect(() => { api.get<any>("/catalog/products").then((r) => setPool(r.items || [])).catch(() => {}); }, []);
  useEffect(() => { api.get<any>("/statuses?record_type=purchase_order").then((r) => setPipeline(r.items || [])).catch(() => {}); }, []);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2200); };

  function startEdit() {
    if (!po) return;
    setEd({
      supplier_invoice_no: po.supplier_invoice_no || "", supplier_invoice_date: po.supplier_invoice_date || "",
      expected_at: po.expected_at || "", notes: po.notes || "", on_credit: !!po.on_credit,
      charges_to_cost: !!po.charges_to_cost, alloc_basis: po.alloc_basis || "value",
      charges: (po.charges || []).map((c) => ({ ...c })),
      lines: po.lines.map((l) => ({ id: l.id, part_id: l.part_id, qty_ordered: l.qty_ordered, unit_cost: l.unit_cost, qty_received: l.qty_received })),
    });
    setEditing(true);
  }
  const edSet = (patch: any) => setEd((e: any) => ({ ...e, ...patch }));
  const edLine = (i: number, patch: any) => setEd((e: any) => ({ ...e, lines: e.lines.map((l: any, j: number) => j === i ? { ...l, ...patch } : l) }));
  const edAddLine = () => setEd((e: any) => ({ ...e, lines: [...e.lines, { part_id: "", qty_ordered: 1, unit_cost: 0 }] }));
  const edDelLine = (i: number) => setEd((e: any) => ({ ...e, lines: e.lines.filter((_: any, j: number) => j !== i) }));
  const edCharge = (i: number, patch: any) => setEd((e: any) => ({ ...e, charges: e.charges.map((c: any, j: number) => j === i ? { ...c, ...patch } : c) }));
  const edAddCharge = () => setEd((e: any) => ({ ...e, charges: [...(e.charges || []), { label: "Shipping", amount: 0 }] }));
  const edDelCharge = (i: number) => setEd((e: any) => ({ ...e, charges: e.charges.filter((_: any, j: number) => j !== i) }));

  async function saveEdit() {
    if (!po || !ed) return;
    const lines = ed.lines.filter((l: any) => l.part_id && l.qty_ordered > 0)
      .map((l: any) => ({ id: l.id, part_id: l.part_id, qty_ordered: Number(l.qty_ordered), unit_cost: Number(l.unit_cost) || 0 }));
    if (lines.length === 0) { flash("Add at least one line"); return; }
    setBusy(true);
    try {
      await api.patch(`/inventory/po/${po.id}`, {
        supplier_invoice_no: ed.supplier_invoice_no || null,
        supplier_invoice_date: ed.supplier_invoice_date || null,
        expected_at: ed.expected_at || null, notes: ed.notes || null,
        on_credit: ed.on_credit, charges_to_cost: ed.charges_to_cost, alloc_basis: ed.alloc_basis,
        charges: (ed.charges || []).filter((c: any) => (c.label || "").trim() || c.amount).map((c: any) => ({ label: c.label || "Charge", amount: Number(c.amount) || 0 })),
        lines,
      });
      flash("Saved"); setEditing(false); setEd(null); onChanged(); load();
    } catch (e: any) { flash(e.message || "Couldn't save"); } finally { setBusy(false); }
  }

  async function sendPo(channel: string) {
    if (!po) return;
    setSendMenu(false); setBusy(true);
    try {
      const r = await api.post<any>(`/inventory/po/${po.id}/send`, { channel });
      flash(r.logged ? `Sent to supplier via ${channel}` : `Sent (${channel}) — no supplier contact on file`);
    } catch (e: any) { flash(e.message || "Couldn't send"); } finally { setBusy(false); }
  }

  const prodName = (id: string) => pool.find((p) => p.id === id)?.name || id;

  // barcode receiving: scan a code → match a PO line by the product's sku/barcode/code → +1 to receive
  function onScan(code: string) {
    const c = code.trim().toLowerCase();
    if (!c || !po) return;
    const p: any = pool.find((x: any) => [x.sku, x.barcode, x.code].some((v: any) => (v || "").toLowerCase() === c));
    const line = p && po.lines.find((l) => l.part_id === p.id);
    if (!line) { flash(`No PO line matches "${code}"`); setScan(""); return; }
    const remain = Math.max((line.qty_ordered || 0) - (line.qty_received || 0), 0);
    const cur = recv[line.id] ?? 0;
    if (cur >= remain) { flash(`${prodName(line.part_id)} already at max (${remain})`); setScan(""); return; }
    setRecv({ ...recv, [line.id]: cur + 1 });
    flash(`+1 ${prodName(line.part_id)}`); setScan("");
  }

  const outstanding = (po?.lines || []).reduce((n, l) => n + Math.max((l.qty_ordered || 0) - (l.qty_received || 0), 0), 0);
  const toReceive = Object.values(recv).reduce((n, q) => n + (q > 0 ? q : 0), 0);
  const received = (po?.status === "received");
  const st = (po?.status || "").toLowerCase();
  // Receiving is only open once the PO is placed (Ordered) and while stock is still
  // outstanding — mirrors ShopMonkey/ServiceTitan: a Draft is edited, an Ordered PO is received.
  const canReceive = (st === "ordered" || st === "partial") && outstanding > 0;

  const [shipFreight, setShipFreight] = useState("");
  async function receive(all = false) {
    if (!po) return;
    const source = all
      ? po.lines.map((l) => ({ po_line_id: l.id, qty: Math.max((l.qty_ordered || 0) - (l.qty_received || 0), 0) })).filter((l) => l.qty > 0)
      : po.lines.filter((l) => (recv[l.id] || 0) > 0).map((l) => ({ po_line_id: l.id, qty: recv[l.id] }));
    if (source.length === 0) { flash("Nothing to receive — set a quantity"); return; }
    setBusy(true);
    try {
      const body: any = { lines: source };
      const sf = Number(shipFreight);
      if (sf > 0) { body.shipment_freight = sf; body.shipment_freight_to_cost = true; }  // 144ar
      const r = await api.post<any>(`/inventory/po/${po.id}/receive`, body);
      flash(r.status === "received" ? "Fully received — stock posted" : "Partially received — stock posted");
      setShipFreight(""); onChanged(); load();
    } catch (e: any) { flash(e.message || "Receive failed"); } finally { setBusy(false); }
  }

  async function setStatus(status: string) {
    if (!po) return;
    setStatusMenu(false); setBusy(true);
    try {
      const r = await api.post<any>(`/inventory/po/${po.id}/status`, { status });
      flash(`Status: ${humanize(r.status)}`); onChanged(); load();
    } catch (e: any) { flash(e.message || "Couldn't change status"); } finally { setBusy(false); }
  }

  // 144an — manual, configurable workflow stage (Orderry parity). Independent of receipt state.
  async function setWorkflow(key: string) {
    if (!po) return;
    setStatusMenu(false); setBusy(true);
    try {
      await api.post(`/inventory/po/${po.id}/workflow`, { workflow_status: key });
      onChanged(); load();
    } catch (e: any) { flash(e.message || "Couldn't change status"); } finally { setBusy(false); }
  }

  const [freight, setFreight] = useState("");
  async function addFreightRecost() {
    if (!po) return;
    const amt = Number(freight);
    if (!amt) { flash("Enter a freight amount"); return; }
    setBusy(true);
    try {
      const existing = (po.charges || []).filter((c) => (c.label || "").toLowerCase() !== "freight");
      const r = await api.post<any>(`/inventory/po/${po.id}/recost`, {
        charges: [...existing, { label: "Freight", amount: amt }], charges_to_cost: true,
      });
      flash(r.applied ? `Recosted stock — ${money(r.applied)} freight into inventory` : "Saved (no on-hand stock to recost)");
      setFreight(""); onChanged(); load();
    } catch (e: any) { flash(e.message || "Couldn't recost"); } finally { setBusy(false); }
  }

  async function saveInvoice() {
    if (!po) return;
    setBusy(true);
    try {
      await api.post(`/inventory/po/${po.id}/invoice`, { supplier_invoice_total: inv === "" ? null : Number(inv) });
      flash("Supplier invoice saved"); load();
    } catch (e: any) { flash(e.message || "Couldn't save invoice"); } finally { setBusy(false); }
  }

  async function attachInvoice(file: File) {
    if (!po) return;
    if (file.size > 6_000_000) { flash("File too large (max ~6 MB)"); return; }
    setBusy(true);
    try {
      const dataUrl: string = await new Promise((res, rej) => {
        const r = new FileReader(); r.onload = () => res(r.result as string); r.onerror = rej; r.readAsDataURL(file);
      });
      await api.post(`/inventory/po/${po.id}/invoice`, { invoice_file: dataUrl, invoice_name: file.name });
      flash("Invoice attached"); load();
    } catch (e: any) { flash(e.message || "Couldn't attach"); } finally { setBusy(false); }
  }

  async function cancelPo() {
    if (!po || !(await confirmDialog(`Cancel ${po.po_number}? This can't be undone.`, { danger: true, confirmLabel: "Delete" }))) return;
    setBusy(true);
    try {
      await api.post(`/inventory/po/${po.id}/cancel`, {});
      flash("Purchase order cancelled"); onChanged(); load();
    } catch (e: any) { flash(e.message || "Couldn't cancel"); } finally { setBusy(false); }
  }

  const MATCH: Record<string, { label: string; cls: string; icon: string }> = {
    pending: { label: "Awaiting receipt", cls: "bg-slate-100 text-slate-600 border-slate-200", icon: "schedule" },
    unbilled: { label: "Awaiting invoice", cls: "bg-blue-50 text-primary border-blue-200/60", icon: "request_quote" },
    matched: { label: "3-way matched", cls: "bg-emerald-50 text-emerald-700 border-emerald-200/60", icon: "verified" },
    variance: { label: "Variance", cls: "bg-rose-50 text-rose-700 border-rose-200/60", icon: "report" },
  };

  const stCls = (s?: string) => s === "received" ? "bg-emerald-50 text-emerald-700 border-emerald-200/60"
    : s === "partial" ? "bg-amber-50 text-amber-700 border-amber-200/60"
      : s === "ordered" ? "bg-blue-50 text-primary border-blue-200/60"
        : s === "cancelled" || s === "canceled" ? "bg-rose-50 text-rose-600 border-rose-200/60"
          : "bg-slate-100 text-slate-600 border-slate-200";
  const stLabel: Record<string, string> = {
    draft: "Draft", ordered: "Ordered", partial: "Partially received",
    received: "Received", cancelled: "Cancelled", canceled: "Cancelled", grouped: "Grouped",
  };

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[720px] bg-white dark:bg-[#151b2e] shadow-clay-card flex flex-col border-l border-slate-200/90">
        {/* header */}
        <div className="px-6 pt-5 pb-3 border-b border-slate-100 flex items-start justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-10 h-10 rounded-xl bg-blue-50 text-primary grid place-items-center"><M n="receipt_long" c="text-xl" /></div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-bold text-ink-primary text-lg leading-tight font-mono">{po?.po_number || "Purchase order"}</h2>
                {/* 144an — manual, configurable WORKFLOW stage (primary). Set from the pipeline. */}
                {po && (() => {
                  const wf = pipeline.find((s) => s.key === po.workflow_status);
                  const editable2 = !editing && st !== "cancelled" && st !== "canceled";
                  return (
                    <div className="relative">
                      <button onClick={() => { if (editable2) setStatusMenu((v) => !v); }} disabled={busy}
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${editable2 ? "hover:brightness-95" : "cursor-default"}`}
                        style={wf ? { backgroundColor: wf.color + "22", color: wf.color, borderColor: wf.color + "55" } : undefined}>
                        {wf?.name || po.workflow_status || "Draft"}
                        {editable2 && <M n="expand_more" c="text-[13px]" />}
                      </button>
                      {statusMenu && (
                        <div className="absolute left-0 top-8 z-20 w-52 max-h-72 overflow-y-auto bg-white dark:bg-[#1c2439] rounded-xl shadow-clay-card border border-slate-200/80 py-1 text-xs">
                          <div className="px-3 py-1.5 text-[11px] font-bold text-ink-tertiary">Set status</div>
                          {pipeline.map((s) => (
                            <button key={s.key} onClick={() => setWorkflow(s.key)}
                              className={`w-full text-left px-3 py-2 hover:bg-slate-100 font-semibold flex items-center gap-2 ${s.key === po.workflow_status ? "text-ink-primary" : "text-ink-secondary"}`}>
                              <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: s.color }} />{s.name}
                              {s.key === po.workflow_status && <M n="check" c="text-sm text-primary ml-auto" />}
                            </button>
                          ))}
                          {pipeline.length === 0 && <div className="px-3 py-2 text-ink-tertiary">Configure stages in Settings → Statuses → Purchase orders.</div>}
                        </div>
                      )}
                    </div>
                  );
                })()}
                {/* Receipt state (auto, read-only) — never hand-set; driven by actual receiving. */}
                {po && st !== "draft" && (
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${stCls(st)}`} title="Receipt state — updates automatically as you receive stock">
                    <M n={st === "received" ? "check_circle" : "local_shipping"} c="text-[13px]" />{stLabel[st] || po.status}</span>
                )}
                {po?.match && MATCH[po.match.status] && (
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${MATCH[po.match.status].cls}`}>
                    <M n={MATCH[po.match.status].icon} c="text-[13px]" />{MATCH[po.match.status].label}</span>
                )}
              </div>
              <span className="text-[11px] text-ink-tertiary">{po?.supplier?.name || "—"} · ordered {d(po?.ordered_at)} · expected {d(po?.expected_at)}</span>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {po?.editable && !editing && <button aria-label="Edit PO" onClick={startEdit} disabled={busy} title="Edit PO" className="w-8 h-8 rounded-lg grid place-items-center text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="edit" c="text-base" /></button>}
            {po && !received && po.status !== "cancelled" && !editing && (
              <div className="relative">
                <button aria-label="Send to supplier" onClick={() => setSendMenu((v) => !v)} disabled={busy} title="Send to supplier" className="w-8 h-8 rounded-lg grid place-items-center text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="send" c="text-base" /></button>
                {sendMenu && (
                  <div className="absolute right-0 top-9 z-10 w-40 bg-white dark:bg-[#1c2439] rounded-xl shadow-clay-card border border-slate-200/80 py-1 text-xs">
                    {[["email", "Email"], ["sms", "SMS"], ["whatsapp", "WhatsApp"]].map(([c, l]) => (
                      <button key={c} onClick={() => sendPo(c)} className="w-full text-left px-3 py-2 hover:bg-slate-100 text-ink-secondary font-semibold">{l}</button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {po?.editable && !editing && <button aria-label="Cancel PO" onClick={cancelPo} disabled={busy} title="Cancel PO" className="w-8 h-8 rounded-lg grid place-items-center text-ink-tertiary hover:text-rose-600 hover:bg-rose-50"><M n="cancel" c="text-base" /></button>}
            <button aria-label="Print / PDF" onClick={() => openAuthedDoc(`/print/purchase-order/${poId}`).catch(() => flash("Could not open PDF"))} title="Print / PDF" className="w-8 h-8 rounded-lg grid place-items-center text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="print" c="text-base" /></button>
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {!po ? <div className="text-sm text-ink-tertiary">Loading…</div> : editing && ed ? (
            /* ── EDIT MODE (PATCH /po) ── */
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Supplier invoice #</label>
                  <input value={ed.supplier_invoice_no} onChange={(e) => edSet({ supplier_invoice_no: e.target.value })} className="glass-input rounded-lg px-3 py-2 w-full" /></div>
                <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Invoice date</label>
                  <DateField value={ed.supplier_invoice_date} onChange={(v) => edSet({ supplier_invoice_date: v })} /></div>
                <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Expected</label>
                  <DateField value={ed.expected_at} onChange={(v) => edSet({ expected_at: v })} /></div>
                <label className="flex items-end gap-2 pb-2 text-ink-secondary cursor-pointer"><input type="checkbox" checked={ed.on_credit} onChange={(e) => edSet({ on_credit: e.target.checked })} />On credit</label>
              </div>

              {/* editable lines */}
              <div className="rounded-2xl border border-slate-200/80 overflow-hidden">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50/70 text-[11px] font-bold text-ink-tertiary">
                    <tr><th className="py-2 px-3">Product</th><th className="py-2 px-2 text-right w-16">Qty</th><th className="py-2 px-2 text-right w-24">Unit cost</th><th className="w-8" /></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {ed.lines.map((l: any, i: number) => (
                      <tr key={i}>
                        <td className="py-2 px-3">
                          {(l.qty_received || 0) > 0 ? (
                            <span className="font-semibold text-ink-primary">{prodName(l.part_id)} <span className="text-[11px] text-ink-tertiary">· {l.qty_received} received, locked</span></span>
                          ) : (
                            <select value={l.part_id} onChange={(e) => { const p: any = pool.find((x) => x.id === e.target.value); edLine(i, { part_id: e.target.value, unit_cost: l.unit_cost || (p && (p.cost_price ?? p.avg_cost)) || 0 }); }} className="glass-input rounded-lg px-2 py-1.5 w-full">
                              <option value="">Select product…</option>{pool.map((p) => <option key={p.id} value={p.id}>{p.name}{p.sku ? ` · ${p.sku}` : ""}</option>)}
                            </select>
                          )}
                        </td>
                        <td className="py-2 px-2"><input type="number" min={0} value={l.qty_ordered} onChange={(e) => edLine(i, { qty_ordered: Math.max(0, Number(e.target.value) || 0) })} className="glass-input rounded-lg px-2 py-1.5 w-full text-right font-mono" /></td>
                        <td className="py-2 px-2"><input type="number" min={0} value={l.unit_cost} onChange={(e) => edLine(i, { unit_cost: Math.max(0, Number(e.target.value) || 0) })} className="glass-input rounded-lg px-2 py-1.5 w-full text-right font-mono" /></td>
                        <td className="py-2 px-1 text-center">{(l.qty_received || 0) === 0 && <button aria-label="Close" onClick={() => edDelLine(i)} className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button onClick={edAddLine} className="text-xs font-bold text-primary hover:underline flex items-center gap-1"><M n="add" c="text-sm" />Add line</button>

              {/* charges / landed cost */}
              <div className="rounded-2xl border border-slate-200/80 p-3 space-y-2">
                <div className="flex items-center justify-between"><span className="text-[11px] font-bold text-ink-tertiary">Charges (shipping / customs)</span>
                  <button onClick={edAddCharge} className="text-[11px] font-bold text-primary hover:underline">+ Add charge</button></div>
                {(ed.charges || []).length === 0 && <div className="text-[11px] text-ink-tertiary">No charges. Add freight/customs to include them in the PO total.</div>}
                {(ed.charges || []).map((c: any, i: number) => (
                  <div key={i} className="flex items-center gap-2">
                    <input value={c.label} onChange={(e) => edCharge(i, { label: e.target.value })} placeholder="Label" className="glass-input rounded-lg px-2 py-1.5 text-xs flex-1" />
                    <input type="number" min={0} value={c.amount} onChange={(e) => edCharge(i, { amount: Number(e.target.value) || 0 })} className="glass-input rounded-lg px-2 py-1.5 text-xs w-28 text-right font-mono" />
                    <button aria-label="Close" onClick={() => edDelCharge(i)} className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>
                  </div>
                ))}
                <label className="flex items-center gap-2 text-[11px] text-ink-secondary cursor-pointer pt-1"><input type="checkbox" checked={ed.charges_to_cost} onChange={(e) => edSet({ charges_to_cost: e.target.checked })} />Capitalise charges into unit cost (landed cost) at receive</label>
                {ed.charges_to_cost && (
                  <div className="flex items-center gap-2 pt-1 text-[11px] text-ink-secondary">
                    <span>Spread by</span>
                    <select value={ed.alloc_basis || "value"} onChange={(e) => edSet({ alloc_basis: e.target.value })} className="glass-input rounded-lg px-2 py-1 text-[11px]">
                      <option value="value">Value (cost-weighted)</option>
                      <option value="qty">Quantity (per unit)</option>
                      <option value="weight">Weight (per kg)</option>
                    </select>
                  </div>
                )}
              </div>

              <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Notes</label>
                <textarea value={ed.notes} onChange={(e) => edSet({ notes: e.target.value })} rows={2} className="glass-input rounded-lg px-3 py-2 text-xs w-full" /></div>
            </div>
          ) : <>
            {/* supplier doc row */}
            <div className="grid grid-cols-3 gap-3 text-xs">
              {[["Supplier invoice", po.supplier_invoice_no || "—"], ["Invoice date", d(po.supplier_invoice_date)],
                ["Terms", po.on_credit ? "On credit" : "Paid / cash"]].map(([l, v]) => (
                <div key={l} className="rounded-xl bg-slate-50/70 border border-slate-200/70 px-3 py-2">
                  <div className="text-[11px] font-bold text-ink-tertiary">{l}</div>
                  <div className="text-ink-primary font-semibold mt-0.5">{v}</div>
                </div>
              ))}
            </div>

            {/* draft prompt — receiving opens once the PO is placed */}
            {st === "draft" && (
              <div className="flex items-center gap-2 rounded-xl bg-slate-50 border border-slate-200 px-3 py-2.5">
                <M n="edit_note" c="text-base text-ink-tertiary" />
                <span className="text-xs text-ink-secondary flex-1">This PO is a draft — edit freely, then mark it ordered to place it and begin receiving.</span>
                <button onClick={() => setStatus("ordered")} disabled={busy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50">Mark as ordered</button>
              </div>
            )}

            {/* barcode receiving */}
            {canReceive && (
              <div className="flex items-center gap-2 rounded-xl bg-blue-50/60 border border-blue-100 px-3 py-2">
                <M n="barcode_scanner" c="text-base text-primary" />
                <input value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") onScan(scan); }}
                  placeholder="Scan or type a barcode / SKU to add 1 to receive" className="glass-input rounded-lg px-3 py-1.5 text-xs flex-1 font-mono" />
              </div>
            )}

            {/* lines + receive */}
            <div className="rounded-2xl border border-slate-200/80 overflow-hidden">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50/70 text-[11px] font-bold text-ink-tertiary">
                  <tr><th className="py-2.5 px-3">Product</th><th className="py-2.5 px-3 text-right">Ordered</th>
                    <th className="py-2.5 px-3 text-right">Received</th><th className="py-2.5 px-3 text-right">Unit cost</th>
                    {canReceive && <th className="py-2.5 px-3 text-right w-24">Receive now</th>}</tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {po.lines.map((l) => {
                    const remain = Math.max((l.qty_ordered || 0) - (l.qty_received || 0), 0);
                    return (
                      <tr key={l.id}>
                        <td className="py-2.5 px-3 font-semibold text-ink-primary">{l.part || l.part_id}</td>
                        <td className="py-2.5 px-3 text-right tabular-nums">{l.qty_ordered}</td>
                        <td className="py-2.5 px-3 text-right tabular-nums">{l.qty_received || 0}{remain > 0 && (l.qty_received || 0) > 0 ? <span className="text-[11px] text-amber-600"> · {remain} left</span> : null}</td>
                        <td className="py-2.5 px-3 text-right tabular-nums">{money(l.unit_cost)}</td>
                        {canReceive && <td className="py-2.5 px-3 text-right">
                          <input type="number" min={0} max={remain} value={recv[l.id] ?? 0}
                            onChange={(e) => setRecv({ ...recv, [l.id]: Math.max(0, Math.min(remain, Number(e.target.value) || 0)) })}
                            disabled={remain === 0}
                            className="glass-input rounded-lg px-2 py-1 w-20 text-right tabular-nums disabled:opacity-40" />
                        </td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* charges + totals */}
            <div className="ml-auto w-full max-w-[280px] text-xs space-y-1">
              <div className="flex justify-between text-ink-secondary"><span>Subtotal</span><span className="tabular-nums">{money(po.subtotal)}</span></div>
              {(po.charges || []).map((c, i) => (
                <div key={i} className="flex justify-between text-ink-tertiary"><span>{c.label}</span><span className="tabular-nums">{money(c.amount)}</span></div>
              ))}
              {po.charges_to_cost && (po.charges_total || 0) > 0 && <div className="text-[11px] text-ink-tertiary italic">Charges capitalised into unit cost</div>}
              <div className="flex justify-between font-bold text-ink-primary border-t border-slate-200 pt-1"><span>Total</span><span className="tabular-nums">{money(po.total)}</span></div>
            </div>

            {/* 144ap — In transit ("on the water") + late-freight recost */}
            {(() => {
              const inTransitQty = (po.lines || []).reduce((n, l) => n + Math.max((l.qty_ordered || 0) - (l.qty_received || 0), 0), 0);
              const inTransitVal = (po.lines || []).reduce((n, l) => n + Math.max((l.qty_ordered || 0) - (l.qty_received || 0), 0) * (l.unit_cost || 0), 0);
              const anyRecvd = (po.lines || []).some((l) => (l.qty_received || 0) > 0);
              if (!inTransitQty && !anyRecvd) return null;
              return (
                <div className="rounded-2xl border border-slate-200/80 p-4 space-y-3">
                  {inTransitQty > 0 && (
                    <div className="flex items-center gap-2 text-xs">
                      <span className="w-7 h-7 rounded-lg bg-amber-50 text-amber-600 grid place-items-center border border-amber-100"><M n="local_shipping" c="text-base" /></span>
                      <span className="text-ink-secondary font-semibold">On the water:</span>
                      <span className="text-ink-primary font-bold">{inTransitQty} unit{inTransitQty === 1 ? "" : "s"}</span>
                      <span className="tabular-nums text-ink-tertiary">· {money(inTransitVal)}</span>
                      {po.expected_at && <span className="text-ink-tertiary ml-auto">ETA {d(po.expected_at)}</span>}
                    </div>
                  )}
                  {anyRecvd && (
                    <div className="flex items-end gap-2 pt-1 border-t border-slate-100">
                      <div className="flex-1">
                        <label className="block text-[11px] font-bold text-ink-tertiary mb-1 flex items-center gap-1"><M n="paid" c="text-sm text-primary" />Freight bill arrived late?</label>
                        <input type="number" min={0} value={freight} onChange={(e) => setFreight(e.target.value)} placeholder="Shipping / customs amount"
                          className="glass-input rounded-lg px-3 py-2 text-sm w-full tabular-nums" />
                      </div>
                      <button onClick={addFreightRecost} disabled={busy || !freight} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50">Add &amp; recost stock</button>
                    </div>
                  )}
                  {anyRecvd && <div className="text-[11px] text-ink-tertiary">Spreads the freight into the cost of stock you've already received — inventory value updates instantly.</div>}
                </div>
              );
            })()}

            {/* three-way match: ordered vs received vs supplier-invoiced */}
            {po.match && po.match.status !== "pending" && (
              <div className="rounded-2xl border border-slate-200/80 p-4 space-y-2.5">
                <div className="flex items-center gap-1.5 text-[11px] font-bold text-ink-tertiary ">
                  <M n="rule" c="text-sm text-primary" />Three-way match
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div><div className="text-[11px] text-ink-tertiary ">Ordered</div><div className="tabular-nums font-semibold text-ink-primary">{po.match.ordered_qty}</div></div>
                  <div><div className="text-[11px] text-ink-tertiary ">Received</div>
                    <div className={`tabular-nums font-semibold ${po.match.qty_variance !== 0 ? "text-rose-600" : "text-ink-primary"}`}>{po.match.received_qty}{po.match.qty_variance !== 0 && <span className="text-[11px]"> ({po.match.qty_variance > 0 ? "+" : ""}{po.match.qty_variance})</span>}</div></div>
                  <div><div className="text-[11px] text-ink-tertiary ">Expected cost</div><div className="tabular-nums font-semibold text-ink-primary">{money(po.match.expected_cost)}</div></div>
                </div>
                <div className="flex items-end gap-2 pt-1">
                  <div className="flex-1">
                    <label className="block text-[11px] font-bold text-ink-tertiary mb-1">Supplier invoice total</label>
                    <input type="number" min={0} value={inv} onChange={(e) => setInv(e.target.value)} placeholder="What they billed you"
                      className="glass-input rounded-lg px-3 py-2 text-sm w-full tabular-nums" />
                  </div>
                  <button onClick={saveInvoice} disabled={busy} className="px-3 py-2 rounded-lg bg-slate-100 text-ink-primary text-xs font-bold hover:bg-slate-200 disabled:opacity-50">Save</button>
                </div>
                <div className="flex items-center gap-2 pt-1">
                  {po.supplier_invoice_file ? (
                    <button onClick={() => openAuthedDoc(po.supplier_invoice_file!.url).catch(() => flash("Could not open file"))}
                      className="text-[11px] font-semibold text-primary hover:underline flex items-center gap-1"><M n="description" c="text-sm" />{po.supplier_invoice_file.name || "Supplier invoice"}</button>
                  ) : <span className="text-[11px] text-ink-tertiary">No invoice file attached.</span>}
                  <label className="ml-auto text-[11px] font-bold text-primary hover:underline cursor-pointer flex items-center gap-1">
                    <M n="attach_file" c="text-sm" />{po.supplier_invoice_file ? "Replace" : "Attach PDF"}
                    <input type="file" accept="application/pdf,image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) attachInvoice(f); e.target.value = ""; }} />
                  </label>
                </div>
                {po.match.price_variance != null && po.match.price_variance !== 0 && (
                  <div className="text-[11px] text-rose-600 font-semibold flex items-center gap-1"><M n="report" c="text-sm" />
                    Billed {money(po.match.invoiced_total || 0)} — {po.match.price_variance > 0 ? "over" : "under"} expected by {money(Math.abs(po.match.price_variance))}</div>
                )}
                {po.match.status === "matched" && <div className="text-[11px] text-emerald-700 font-semibold flex items-center gap-1"><M n="verified" c="text-sm" />Order, receipt and invoice all agree.</div>}
              </div>
            )}

            {/* receipt timeline */}
            {receipts.length > 0 && (
              <div className="rounded-2xl border border-slate-200/80 p-4">
                <div className="flex items-center gap-1.5 text-[11px] font-bold text-ink-tertiary mb-2"><M n="history" c="text-sm text-primary" />Receipt history</div>
                <div className="divide-y divide-slate-100">
                  {receipts.map((r) => (
                    <div key={r.id} className="flex items-center gap-3 py-1.5 text-xs">
                      <span className="tabular-nums text-ink-tertiary w-[92px] shrink-0">{(r.at || "").slice(0, 10)}</span>
                      <span className="text-ink-secondary flex-1 truncate">{r.part}{r.by ? ` · ${r.by}` : ""}</span>
                      <span className="tabular-nums text-ink-primary">+{r.qty}</span>
                      <span className="tabular-nums text-ink-tertiary w-16 text-right">{money(r.unit_cost)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {po.notes && <div className="text-xs text-ink-secondary"><span className="font-bold text-ink-tertiary text-[11px] block mb-0.5">Notes</span>{po.notes}</div>}
          </>}
        </div>

        {/* edit action bar */}
        {po && editing && (
          <div className="px-6 py-3 border-t border-slate-100 flex items-center justify-end gap-2 bg-white dark:bg-[#151b2e]">
            <button onClick={() => { setEditing(false); setEd(null); }} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold hover:bg-slate-100">Cancel</button>
            <button onClick={saveEdit} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50 flex items-center gap-1.5"><M n="check" c="text-sm" />{busy ? "Saving…" : "Save changes"}</button>
          </div>
        )}
        {/* receive action bar */}
        {po && canReceive && !editing && (
          <div className="px-6 py-3 border-t border-slate-100 flex items-center justify-between bg-white dark:bg-[#151b2e]">
            <div className="flex items-center gap-3">
              <span className="text-[11px] text-ink-tertiary">{outstanding} outstanding · receiving {toReceive}</span>
              {/* 144ar — freight billed with THIS delivery, capitalised into these units' cost */}
              <label className="flex items-center gap-1 text-[11px] text-ink-tertiary" title="Freight billed with this delivery — capitalised into the units received now">
                <M n="local_shipping" c="text-sm" />
                <input type="number" min={0} value={shipFreight} onChange={(e) => setShipFreight(e.target.value)}
                  placeholder="Shipment freight" className="glass-input rounded-lg px-2 py-1 w-28 text-right tabular-nums text-[11px]" />
              </label>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={() => receive(true)} disabled={busy}
                className="px-3 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-bold hover:bg-slate-100 disabled:opacity-50 flex items-center gap-1.5">
                <M n="done_all" c="text-sm" />Receive all
              </button>
              <button onClick={() => receive(false)} disabled={busy || toReceive === 0}
                className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50 flex items-center gap-1.5">
                <M n="inventory" c="text-sm" />{busy ? "Receiving…" : `Receive ${toReceive || ""}`.trim()}
              </button>
            </div>
          </div>
        )}
        {po && received && <div className="px-6 py-3 border-t border-slate-100 text-[11px] text-emerald-700 font-semibold flex items-center gap-1.5"><M n="check_circle" c="text-sm" />Fully received {d(po.received_at)} — stock posted with cost layers</div>}
        {po && (st === "cancelled" || st === "canceled") && <div className="px-6 py-3 border-t border-slate-100 text-[11px] text-rose-600 font-semibold flex items-center gap-1.5"><M n="cancel" c="text-sm" />This purchase order was cancelled.</div>}

        {toast && <div className="absolute bottom-16 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2 rounded-lg shadow-clay-card">{toast}</div>}
      </div>
    </div>
  );
}
