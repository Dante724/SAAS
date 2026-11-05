import SkeletonRows from "../../components/SkeletonRows";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import { useDebounced, useServerPage } from "../../hooks/useServerPage";
import AssetDrawer from "../orders/AssetDrawer";
import { useTerminology, assetNoun } from "../../lib/useTerminology";
import Pager from "../../components/Pager";
import { type AssetCounts, type AssetState, useAssetState } from "../../components/AssetStateTabs";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card, { Badge } from "../../components/Card";
import { tableClass, theadRowClass, Th, Td } from "../../components/Table";
import { ErrorState, TableEmpty } from "../../components/EmptyState";
import SearchBox from "../orders/SearchBox";
import { clickableRow } from "../../lib/clickable";
import ImportDrawer from "../../components/ImportDrawer";
import { useMe } from "../../context/Me";

type Asset = { id: string; asset_status?: string; asset_type?: string; asset_label?: string; template_key?: string; brand?: string; model?: string; serial?: string; imei?: string; owner_type?: string; owner_name?: string; condition?: string; order_count?: number };

const STATES: { id: AssetState; label: string }[] = [
  { id: "active", label: "Active" }, { id: "written_off", label: "Written off" }, { id: "all", label: "All" },
];

export default function AssetsHub() {
  const term = useTerminology();
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const { can } = useMe();
  const [q, setQ] = useState("");
  const PAGE_SIZE = 50;

  // Searched and paged on the server (the API serves at most 200 rows per request).
  const dq = useDebounced(q.trim());
  const [state, setState] = useAssetState();
  const sp_ = useServerPage<Asset, { counts?: AssetCounts }>("/assets", { search: dq || undefined, state }, { pageSize: PAGE_SIZE });
  const rows = sp_.rows;
  useEffect(() => { setErr(sp_.err); }, [sp_.err]);
  const reload = () => sp_.reload();
  // deep-open from a scan (?open=<asset id>): open the drawer, then clear the param
  const [sp, setSp] = useSearchParams();
  useEffect(() => {
    const id = sp.get("open");
    if (id) { setOpen(id); setSp((p) => { p.delete("open"); return p; }, { replace: true }); }
  }, []); // eslint-disable-line
  const total = sp_.total;
  const page = sp_.page, setPage = sp_.setPage;
  const pageRows = rows || [];

  return (
    <Shell title={term.assets}>
      <PageLayout>
        <Toolbar
          actions={<>
            <Button variant="primary" onClick={() => setCreating(true)} icon={<M n="add" c="text-sm" />}>New {term.asset.toLowerCase()}</Button>
            {can("can_view_asset_info") && <Button onClick={() => setImporting(true)} icon={<M n="upload" c="text-sm" />}>Import</Button>}
          </>}
          filters={<Tabs look="chips" label={`Show ${term.assets.toLowerCase()}`} value={state} onChange={setState}
            tabs={STATES.map((o) => ({ ...o, count: sp_.extra?.counts ? sp_.extra.counts[o.id] : null }))} />}
          search={<SearchBox value={q} onChange={setQ} label={`Search ${term.assets.toLowerCase()}`} placeholder="Search by name, serial, IMEI or plate…" />}
        />
        <Card padded={false}>
          <div className="overflow-x-auto">
          <table className={tableClass}>
            <thead><tr className={theadRowClass}>
              <Th>Type</Th><Th>{term.asset}</Th><Th>Serial / IMEI</Th>
              <Th>Owner</Th><Th>Condition</Th><Th num>Orders</Th><Th className="w-12"><span className="sr-only">Open</span></Th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {err && <tr><td colSpan={7} className="p-0"><ErrorState body={err} onRetry={reload} /></td></tr>}
              {!rows && !err && <SkeletonRows cols={7} />}
              {rows && rows.length === 0 && !err && <TableEmpty cols={7} icon={<M n="devices" c="text-lg" />}
                title={dq ? `Nothing matches “${dq}”` : state === "written_off" ? `No written-off ${term.assets.toLowerCase()}` : `No ${term.assets.toLowerCase()} yet`}
                body={dq ? "Try a different name, serial or IMEI." : undefined} />}
              {pageRows.map((a) => (
                <tr key={a.id} className="hover:bg-blue-50/40 cursor-pointer transition-colors group" {...clickableRow(() => setOpen(a.id))}>
                  <Td className="text-ink-tertiary">{a.asset_type && a.asset_type !== "Device" ? a.asset_type : assetNoun(a, term)}</Td>
                  <Td className="font-semibold text-ink-primary">{[a.brand, a.model].filter(Boolean).join(" ") || "—"}{a.asset_status === "written_off" && <Badge className="ml-2">Written off</Badge>}</Td>
                  <Td className="font-mono text-2xs">{a.imei || a.serial || "—"}</Td>
                  <Td>{a.owner_type === "company" ? "Company" : a.owner_name || "Client"}</Td>
                  <Td>{a.condition || "—"}</Td>
                  <Td num className="font-semibold text-ink-primary">{a.order_count ?? 0}</Td>
                  <Td className="text-center"><M n="chevron_right" c="text-sm text-ink-tertiary" /></Td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          {rows && total > 0 && <Pager total={total} page={page} pageSize={PAGE_SIZE} onPage={setPage} noun={term.assets.toLowerCase()} />}
        </Card>
      </PageLayout>
      {open && <AssetDrawer assetId={open} onClose={() => setOpen(null)} onSaved={reload} />}
      {creating && <AssetDrawer onClose={() => setCreating(false)} onSaved={reload} />}
      {importing && <ImportDrawer kind="assets" onClose={() => setImporting(false)} onDone={reload} />}
    </Shell>
  );
}
