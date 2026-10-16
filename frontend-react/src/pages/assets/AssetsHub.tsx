import SkeletonRows from "../../components/SkeletonRows";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import AssetDrawer from "../orders/AssetDrawer";
import { useTerminology, assetNoun } from "../../lib/useTerminology";
import Pager from "../../components/Pager";

type Asset = { id: string; asset_type?: string; asset_label?: string; template_key?: string; brand?: string; model?: string; serial?: string; imei?: string; owner_type?: string; owner_name?: string; condition?: string; order_count?: number };

export default function AssetsHub() {
  const term = useTerminology();
  const [rows, setRows] = useState<Asset[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const PAGE_SIZE = 50;

  const reload = () => api.get<{ items: Asset[] }>("/assets?limit=1000").then((r) => setRows(r.items || [])).catch((e) => setErr(String(e.message || e)));
  useEffect(() => { reload(); }, []);
  // deep-open from a scan (?open=<asset id>): open the drawer, then clear the param
  const [sp, setSp] = useSearchParams();
  useEffect(() => {
    const id = sp.get("open");
    if (id) { setOpen(id); setSp((p) => { p.delete("open"); return p; }, { replace: true }); }
  }, []); // eslint-disable-line
  const shown = (rows || []).filter((a) => !q || [a.brand, a.model, a.serial, a.imei, a.owner_name, a.asset_type].some((v) => (v || "").toLowerCase().includes(q.toLowerCase())));
  useEffect(() => { setPage(1); }, [q]);
  const total = shown.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageRows = shown.slice((Math.min(page, pages) - 1) * PAGE_SIZE, Math.min(page, pages) * PAGE_SIZE);

  return (
    <Shell title={term.assets}>
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3.5 flex flex-wrap items-center justify-between gap-3">
          <button onClick={() => setCreating(true)} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5 transition-all"><M n="add" c="text-sm font-bold" />New {term.asset.toLowerCase()}</button>
          <div className="relative w-72"><M n="search" c="absolute left-2.5 top-2 text-ink-tertiary text-sm" /><input value={q} onChange={(e) => setQ(e.target.value)} className="glass-input w-full pl-8 pr-3 py-1.5 text-xs rounded-lg font-medium" placeholder={`Search ${term.assets.toLowerCase()}…`} /></div>
        </div>
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
          <table className="w-full text-left border-collapse text-xs">
            <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
              <th className="py-3 px-4">Type</th><th className="py-3 px-4">{term.asset}</th><th className="py-3 px-4">Serial / IMEI</th>
              <th className="py-3 px-4">Owner</th><th className="py-3 px-4">Condition</th><th className="py-3 px-4">Orders</th><th className="py-3 px-4 w-12 text-center">Action</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {err && <tr><td colSpan={7} className="py-6 px-4 text-status-overdue-ink">Error: {err}</td></tr>}
              {!rows && !err && <SkeletonRows cols={7} />}
              {pageRows.map((a) => (
                <tr key={a.id} className="hover:bg-blue-50/40 cursor-pointer transition-colors group" onClick={() => setOpen(a.id)}>
                  <td className="py-3 px-4 text-ink-tertiary">{a.asset_type && a.asset_type !== "Device" ? a.asset_type : assetNoun(a, term)}</td>
                  <td className="py-3 px-4 font-bold text-ink-primary">{[a.brand, a.model].filter(Boolean).join(" ") || "—"}</td>
                  <td className="py-3 px-4 font-mono text-[11px]">{a.imei || a.serial || "—"}</td>
                  <td className="py-3 px-4"><span className="px-2.5 py-1 rounded-full text-[11px] font-semibold bg-blue-100 text-blue-800 border border-blue-200">{a.owner_type === "company" ? "Company" : a.owner_name || "Client"}</span></td>
                  <td className="py-3 px-4">{a.condition || "—"}</td>
                  <td className="py-3 px-4 tabular-nums font-semibold text-ink-primary">{a.order_count ?? 0}</td>
                  <td className="py-3 px-4 text-center"><button aria-label="Next" className="p-1 rounded text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="chevron_right" c="text-sm" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows && total > 0 && <Pager total={total} page={Math.min(page, pages)} pageSize={PAGE_SIZE} onPage={setPage} noun={term.assets.toLowerCase()} />}
        </div>
      </div>
      {open && <AssetDrawer assetId={open} onClose={() => setOpen(null)} onSaved={reload} />}
      {creating && <AssetDrawer onClose={() => setCreating(false)} onSaved={reload} />}
    </Shell>
  );
}
