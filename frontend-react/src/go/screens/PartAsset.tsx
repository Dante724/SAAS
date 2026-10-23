// Scan targets that aren't jobs: a part/product (stock on hand) and a device/asset (its job history).
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ChevronLeft, ExternalLink, Package } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card, Empty, Page, Section, SkeletonRows } from "../components/ui";
import { JobRow } from "./Today";
import { useStatuses } from "../lib/useGo";

function useBack() {
  const nav = useNavigate();
  return (
    <button onClick={() => (history.length > 1 ? nav(-1) : nav("/scan"))} aria-label="Back"
      className="-ml-2 h-11 min-w-[2.75rem] pr-2 inline-flex items-center text-primary font-medium"><ChevronLeft size={26} />Back</button>
  );
}

function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setData(null); setErr(null);
    api.get<T>(path).then((d) => { if (live) setData(d); }).catch((e) => { if (live) setErr(e?.status === 404 ? "Not found in this shop." : e?.message || "Couldn't load"); });
    return () => { live = false; };
  }, [path]);
  return { data, err };
}

const Row = ({ k, v }: { k: string; v?: React.ReactNode }) =>
  v == null || v === "" ? null : <div className="flex justify-between gap-3 py-2.5 first:pt-0 last:pb-0"><span className="text-ink-secondary">{k}</span><span className="font-medium text-ink-primary text-right break-all">{v}</span></div>;

type Product = { product: { id: string; name: string; sku?: string; barcode?: string; code?: string; brand?: string; sell_price?: number; qty_available?: number; qty_reserved?: number; min_stock?: number; low_stock?: boolean; uom?: string } };

export function Part() {
  const { id = "" } = useParams();
  const back = useBack();
  const { fmt } = useMe();
  const { data, err } = useLoad<Product>(`/catalog/products/${encodeURIComponent(id)}`);
  if (err) return <Page title="Part" back={back}><Empty icon={<AlertTriangle size={22} />} title="Can't open this part" body={err} /></Page>;
  if (!data) return <Page title="Part" back={back}><SkeletonRows n={1} /></Page>;
  const p = data.product;
  const qty = p.qty_available ?? 0;
  return (
    <Page title={p.name} back={back} subtitle={p.brand}>
      <Card className="p-5 flex items-center gap-4">
        <span className={`flex h-14 w-14 items-center justify-center rounded-2xl ${p.low_stock ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"}`}><Package size={26} /></span>
        <div>
          <p className="text-[2rem] font-bold leading-none tabular-nums text-ink-primary">{qty}<span className="ml-1.5 text-[1rem] font-medium text-ink-secondary">{p.uom || "in stock"}</span></p>
          <p className={`mt-1 text-[0.875rem] font-medium ${p.low_stock ? "text-amber-700" : "text-ink-secondary"}`}>{p.low_stock ? "Low stock" : "In stock"}{p.qty_reserved ? ` · ${p.qty_reserved} reserved` : ""}</p>
        </div>
      </Card>
      <Card className="mt-3 p-4">
        <Row k="Price" v={p.sell_price != null ? fmt(p.sell_price) : undefined} />
        <Row k="SKU" v={p.sku} />
        <Row k="Barcode" v={p.barcode} />
        <Row k="Code" v={p.code} />
        <Row k="Reorder at" v={p.min_stock || undefined} />
      </Card>
      <a href={`/inventory?open=${encodeURIComponent(p.id)}`} className="mt-3 min-h-[3rem] w-full rounded-2xl border border-slate-200 bg-white inline-flex items-center justify-center gap-2 font-semibold text-ink-primary active:bg-slate-50">
        <ExternalLink size={18} />Open in Tekly
      </a>
    </Page>
  );
}

type Asset = { id: string; brand?: string; model?: string; asset_label?: string; asset_type?: string; serial?: string; imei?: string; vin?: string; owner_name?: string;
  history: { id: string; order_number: string; status: string; total?: number; created_at?: string }[] };

export function AssetScreen() {
  const { id = "" } = useParams();
  const back = useBack();
  const { byKey } = useStatuses();
  const { data, err } = useLoad<Asset>(`/assets/${encodeURIComponent(id)}`);
  const noun = data?.asset_label || data?.asset_type || "Device";
  if (err) return <Page title="Device" back={back}><Empty icon={<AlertTriangle size={22} />} title="Can't open this device" body={err} /></Page>;
  if (!data) return <Page title="Device" back={back}><SkeletonRows n={2} /></Page>;
  return (
    <Page title={[data.brand, data.model].filter(Boolean).join(" ") || noun} back={back} subtitle={data.owner_name ? `${noun} · ${data.owner_name}` : noun}>
      <Card className="p-4">
        <Row k="Serial" v={data.serial} />
        <Row k="IMEI" v={data.imei} />
        <Row k="VIN" v={data.vin} />
      </Card>
      <div className="mt-6">
        <Section title="Jobs" count={data.history.length}>
          {data.history.length
            ? <div className="space-y-2.5">{data.history.map((h) => <JobRow key={h.id} job={{ id: h.id, order_number: h.order_number, status: h.status, scheduled_for: h.created_at }} meta={byKey.get(h.status)} />)}</div>
            : <Empty icon={<Package size={22} />} title="No jobs yet" body={`This ${noun.toLowerCase()} hasn't been in for a job.`} />}
        </Section>
      </div>
    </Page>
  );
}
