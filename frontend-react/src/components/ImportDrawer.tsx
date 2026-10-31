// Import centre drawer — one per list (Products, Services, Clients, Assets, Suppliers).
// 1 Download a template or upload an XLSX/CSV → 2 Check the columns (auto-matched, remembered)
// → 3 Review every row (green create, blue update, red needs fixing) → Import. The server does the
// checking (POST /imports/{kind}/preview is a dry run); this only shows it.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, downloadAuthed } from "../api/client";
import { M } from "./Shell";
import { toXlsx } from "../lib/sheet";
import {
  countBy, errorsSheet, KIND_NOUN, missingRequired, sampleValues,
  type FieldDef, type ImportKind, type Parsed, type RowVerdict,
} from "../lib/importRows";

type Batch = { id: string; file_name: string | null; created: number; updated: number; created_at: string; can_undo: boolean; undone_at: string | null };
type Step = "start" | "columns" | "review" | "done";

const btnPrimary = "h-9 px-4 rounded-lg bg-primary text-white text-sm font-semibold hover:bg-primary-container disabled:opacity-50 flex items-center gap-1.5";
const btnGhost = "h-9 px-4 rounded-lg bg-white border border-slate-200 text-ink-secondary text-sm font-semibold hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1.5";
const fld = "bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-1.5";
const TONE: Record<RowVerdict["action"], string> = {
  create: "bg-emerald-50 text-emerald-700 border-emerald-200",
  update: "bg-blue-50 text-primary border-blue-200",
  error: "bg-rose-50 text-rose-700 border-rose-200",
  skip: "bg-slate-50 text-ink-tertiary border-slate-200",
};
const ACTION_LABEL: Record<RowVerdict["action"], string> = { create: "New", update: "Update", error: "Fix", skip: "Skipped" };

function saveBlob(data: BlobPart, name: string) {
  const url = URL.createObjectURL(new Blob([data], { type: "application/octet-stream" }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export default function ImportDrawer({ kind, onClose, onDone }: { kind: ImportKind; onClose: () => void; onDone: () => void }) {
  const [one, many] = KIND_NOUN[kind];
  const [step, setStep] = useState<Step>("start");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldDef[]>([]);
  const [matchOn, setMatchOn] = useState("");
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [opts, setOpts] = useState<{ location_id?: string; create_categories: boolean; generate_serials: boolean }>({ create_categories: false, generate_serials: false });
  const [verdicts, setVerdicts] = useState<RowVerdict[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [filter, setFilter] = useState<RowVerdict["action"] | "all">("all");
  const [result, setResult] = useState<{ batch_id: string | null; created: number; updated: number; failed: number } | null>(null);
  const [last, setLast] = useState<Batch | null>(null);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadLast = () => api.get<{ items: Batch[] }>(`/imports/batches?kind=${kind}`).then((r) => setLast(r.items?.[0] || null)).catch(() => setLast(null));
  useEffect(() => {
    api.get<any>("/imports/kinds").then((r) => { setFields(r[kind]?.fields || []); setMatchOn(r[kind]?.match_on || ""); }).catch(() => {});
    loadLast();
    if (kind === "products") api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); if (it[0]) setOpts((o) => ({ ...o, location_id: it[0].id })); }).catch(() => {});
  }, [kind]); // eslint-disable-line

  async function upload(f: File) {
    setBusy(true); setErr(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const p = await api.postForm<Parsed>(`/imports/parse?kind=${kind}`, fd);
      setParsed(p); setMapping(p.suggested || {}); setStep("columns");
    } catch (e: any) { setErr(e.message || "Couldn't read that file"); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  const body = () => ({ headers: parsed!.headers, rows: parsed!.rows, row_numbers: parsed!.row_numbers, mapping, options: opts, file_name: parsed!.file_name });
  async function check() {
    if (!parsed) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>(`/imports/${kind}/preview`, body());
      setVerdicts(r.rows || []); setWarnings(r.warnings || []); setFilter("all"); setStep("review");
    } catch (e: any) { setErr(e.message || "Couldn't check the rows"); }
    finally { setBusy(false); }
  }
  async function commit() {
    if (!parsed) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>(`/imports/${kind}/commit`, body());
      setVerdicts(r.rows || []); setResult(r); setStep("done"); loadLast(); onDone();
    } catch (e: any) { setErr(e.message || "Import failed"); }
    finally { setBusy(false); }
  }
  async function undo(id: string) {
    setBusy(true); setErr(null);
    try { await api.post(`/imports/batches/${id}/undo`, {}); await loadLast(); setResult(null); onDone(); if (step === "done") onClose(); }
    catch (e: any) { setErr(e.message || "Couldn't undo"); }
    finally { setBusy(false); }
  }
  function downloadErrors(fmt: "xlsx" | "csv") {
    if (!parsed) return;
    const rows = errorsSheet(parsed, verdicts);
    const base = `${many}-to-fix`;
    if (fmt === "xlsx") saveBlob(toXlsx(rows, "To fix") as BlobPart, `${base}.xlsx`);
    else saveBlob("﻿" + rows.map((r) => r.map((v) => /[",\r\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v)).join(",")).join("\r\n"), `${base}.csv`);
  }
  function setField(header: string, key: string) {
    const next: Record<string, string> = {};
    Object.entries(mapping).forEach(([h, k]) => { if (h !== header && k !== key) next[h] = k; });
    if (key) next[header] = key;
    setMapping(next);
  }

  const samples = useMemo(() => (parsed ? sampleValues(parsed) : []), [parsed]);
  const counts = countBy(verdicts);
  const missing = missingRequired(fields, mapping);
  const mapped = new Set(Object.values(mapping));
  const shown = verdicts.filter((v) => filter === "all" || v.action === filter);
  const toSave = counts.create + counts.update;
  const hasCat = mapped.has("category");
  const STEPS: [Step, string][] = [["start", "File"], ["columns", "Columns"], ["review", "Review"]];
  const stepIdx = step === "done" ? 3 : STEPS.findIndex(([s]) => s === step);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`Import ${many}`}>
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={busy ? undefined : onClose} />
      <div className="relative w-[760px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <div>
            <h2 className="font-bold text-ink-primary">Import {many}</h2>
            <ol className="flex items-center gap-2 mt-1 text-[11px] font-semibold">
              {STEPS.map(([s, label], i) => (
                <li key={s} className={`flex items-center gap-1 ${i <= stepIdx ? "text-primary" : "text-ink-tertiary"}`}>
                  <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[11px] ${i < stepIdx ? "bg-primary text-white" : i === stepIdx ? "border border-primary" : "border border-slate-300"}`}>{i < stepIdx ? <M n="check" c="text-[11px]" /> : i + 1}</span>
                  {label}{i < STEPS.length - 1 && <M n="chevron_right" c="text-sm text-ink-tertiary" />}
                </li>
              ))}
            </ol>
          </div>
          <button aria-label="Close" onClick={onClose} disabled={busy} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

          {step === "start" && (<>
            {last?.can_undo && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs flex flex-wrap items-center gap-3">
                <M n="history" c="text-base text-amber-700" />
                <span className="text-amber-800 flex-1 min-w-[200px]">Last import{last.file_name ? ` (${last.file_name})` : ""}: {last.created} new, {last.updated} updated. You can undo it for 24 hours.</span>
                <button onClick={() => undo(last.id)} disabled={busy} className="px-3 py-1.5 rounded-lg bg-white border border-amber-300 text-amber-800 font-semibold hover:bg-amber-100">Undo last import</button>
              </div>
            )}
            <div className="grid sm:grid-cols-2 gap-3">
              <div className="rounded-xl border border-slate-200 p-4 space-y-2">
                <div className="flex items-center gap-2 font-semibold text-sm text-ink-primary"><M n="download" c="text-base text-primary" />1. Get the template</div>
                <p className="text-xs text-ink-secondary">An Excel file with the right columns, dropdowns and an instructions sheet. Required columns are marked *.</p>
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <button onClick={() => downloadAuthed(`/imports/${kind}/template`).catch((e) => setErr(e.message))} className={btnPrimary}><M n="table_view" c="text-sm" />Download Excel template</button>
                  <button onClick={() => downloadAuthed(`/imports/${kind}/template?fmt=csv`).catch((e) => setErr(e.message))} className="text-xs font-semibold text-primary hover:underline">CSV instead</button>
                </div>
              </div>
              <div
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) upload(f); }}
                className={`rounded-xl border-2 border-dashed p-4 space-y-2 transition-colors ${drag ? "border-primary bg-blue-50/60" : "border-slate-300"}`}>
                <div className="flex items-center gap-2 font-semibold text-sm text-ink-primary"><M n="upload_file" c="text-base text-primary" />2. Upload your file</div>
                <p className="text-xs text-ink-secondary">Our template or your own sheet (.xlsx or .csv, up to 10,000 rows). Nothing is saved until you confirm.</p>
                <input ref={fileRef} type="file" accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
                <button onClick={() => fileRef.current?.click()} disabled={busy} className={btnGhost}><M n="attach_file" c="text-sm" />{busy ? "Reading…" : "Choose file"}</button>
                <p className="text-[11px] text-ink-tertiary">or drop it here</p>
              </div>
            </div>
            {matchOn && <p className="text-xs text-ink-tertiary">Existing {many} are found by {matchOn} and updated — blank cells never clear a value.</p>}
          </>)}

          {step === "columns" && parsed && (<>
            <p className="text-xs text-ink-secondary"><b className="text-ink-primary">{parsed.file_name || "Your file"}</b> · {parsed.total.toLocaleString()} row{parsed.total === 1 ? "" : "s"}. We matched the columns we recognised — check them, and pick a field for any we missed. Your choice is remembered next time.</p>
            {missing.length > 0 && <div className="rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs px-3 py-2">No column for {missing.map((f) => f.label).join(", ")} — new {many} need it, so only updates to existing {many} will work.</div>}
            <div className="rounded-xl border border-slate-200 overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 text-ink-tertiary"><tr><th className="text-left font-semibold py-2 px-3">Column in your file</th><th className="text-left font-semibold py-2 px-3">First value</th><th className="text-left font-semibold py-2 px-3">Imports as</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {parsed.headers.map((h, i) => (
                    <tr key={h}>
                      <td className="py-2 px-3 font-semibold text-ink-primary">{h}</td>
                      <td className="py-2 px-3 text-ink-secondary truncate max-w-[180px]" title={samples[i]}>{samples[i] || <span className="text-ink-tertiary">—</span>}</td>
                      <td className="py-2 px-3">
                        <select aria-label={`Field for ${h}`} value={mapping[h] || ""} onChange={(e) => setField(h, e.target.value)} className={`${fld} w-full ${mapping[h] ? "" : "text-ink-tertiary"}`}>
                          <option value="">Don't import</option>
                          {fields.map((f) => <option key={f.key} value={f.key} disabled={mapped.has(f.key) && mapping[h] !== f.key}>{f.label}{f.required ? " *" : ""}</option>)}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(kind === "products" || kind === "services") && (
              <div className="rounded-xl border border-slate-200 p-3 space-y-2 text-xs">
                {kind === "products" && (mapped.has("opening_qty") || mapped.has("serials")) && (
                  <label className="flex items-center gap-2 text-ink-secondary">Post opening stock into
                    <select aria-label="Warehouse" value={opts.location_id || ""} onChange={(e) => setOpts({ ...opts, location_id: e.target.value })} className={fld}>
                      {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                    </select>
                  </label>
                )}
                {hasCat && <label className="flex items-center gap-2 text-ink-secondary"><input type="checkbox" checked={opts.create_categories} onChange={(e) => setOpts({ ...opts, create_categories: e.target.checked })} />Create missing categories</label>}
                {kind === "products" && mapped.has("opening_qty") && <label className="flex items-center gap-2 text-ink-secondary"><input type="checkbox" checked={opts.generate_serials} onChange={(e) => setOpts({ ...opts, generate_serials: e.target.checked })} />Generate missing serial numbers for serialized products</label>}
                {!hasCat && !(kind === "products" && (mapped.has("opening_qty") || mapped.has("serials"))) && <p className="text-ink-tertiary">No extra options for these columns.</p>}
              </div>
            )}
          </>)}

          {(step === "review" || step === "done") && parsed && (<>
            {step === "done" && result && (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 flex items-start gap-2">
                <M n="check_circle" c="text-base text-emerald-600 mt-0.5" />
                <div>Imported {result.created} new and updated {result.updated} {result.created + result.updated === 1 ? one : many}.
                  {result.failed > 0 && <> {result.failed} row{result.failed === 1 ? "" : "s"} still need fixing — download them, fix and upload again.</>}
                  {result.batch_id && <div className="text-xs text-emerald-700 mt-0.5">Changed your mind? You can undo this import for 24 hours.</div>}
                </div>
              </div>
            )}
            {warnings.map((w) => <div key={w} className="rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs px-3 py-2">{w}</div>)}
            <div className="flex flex-wrap items-center gap-2">
              {([["all", `All ${verdicts.length}`], ["create", `${counts.create} new`], ["update", `${counts.update} update`], ["error", `${counts.error} to fix`], ["skip", `${counts.skip} skipped`]] as const)
                .filter(([k]) => k === "all" || counts[k as RowVerdict["action"]] > 0)
                .map(([k, label]) => (
                  <button key={k} onClick={() => setFilter(k)} aria-pressed={filter === k}
                    className={`px-2.5 py-1 rounded-full border text-xs font-semibold ${k === "all" ? (filter === k ? "bg-ink-primary text-white border-ink-primary" : "bg-white text-ink-secondary border-slate-200") : `${TONE[k as RowVerdict["action"]]} ${filter === k ? "ring-2 ring-offset-1 ring-primary/40" : ""}`}`}>{label}</button>
                ))}
              {counts.error > 0 && <div className="flex items-center gap-2 sm:ml-auto">
                <button onClick={() => downloadErrors("xlsx")} className="text-xs font-semibold text-primary flex items-center gap-1 hover:underline"><M n="download" c="text-sm" />Rows to fix (.xlsx)</button>
                <button onClick={() => downloadErrors("csv")} className="text-xs font-semibold text-primary hover:underline">.csv</button>
              </div>}
            </div>
            <div className="rounded-xl border border-slate-200 overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 text-ink-tertiary"><tr><th className="text-left font-semibold py-2 px-3 w-14">Row</th><th className="text-left font-semibold py-2 px-3 w-20">Result</th><th className="text-left font-semibold py-2 px-3">{one[0].toUpperCase() + one.slice(1)}</th><th className="text-left font-semibold py-2 px-3">Notes</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {shown.slice(0, 500).map((v) => (
                    <tr key={v.row} className="align-top">
                      <td className="py-2 px-3 tabular-nums text-ink-tertiary">{v.row}</td>
                      <td className="py-2 px-3"><span className={`inline-block px-2 py-0.5 rounded-full border text-[11px] font-semibold ${TONE[v.action]}`}>{ACTION_LABEL[v.action]}</span></td>
                      <td className="py-2 px-3 font-semibold text-ink-primary">{v.label || <span className="text-ink-tertiary font-normal">—</span>}{v.matched_on && <span className="block text-[11px] font-normal text-ink-tertiary">matched by {v.matched_on}</span>}</td>
                      <td className="py-2 px-3">
                        {v.errors.map((m) => <div key={m} className="text-rose-700">{m}</div>)}
                        {v.warnings.map((m) => <div key={m} className="text-amber-700">{m}</div>)}
                      </td>
                    </tr>
                  ))}
                  {shown.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-ink-tertiary">No rows here.</td></tr>}
                </tbody>
              </table>
              {shown.length > 500 && <p className="text-[11px] text-ink-tertiary px-3 py-2 border-t border-slate-100">Showing the first 500 of {shown.length}. Download the rows to fix to see all problems.</p>}
            </div>
          </>)}
        </div>

        <div className="px-5 py-4 border-t border-slate-200 flex flex-wrap items-center gap-2">
          {step === "columns" && <>
            <button onClick={check} disabled={busy || Object.keys(mapping).length === 0} className={btnPrimary}><M n="fact_check" c="text-sm" />{busy ? "Checking…" : "Check rows"}</button>
            <button onClick={() => { setStep("start"); setParsed(null); }} disabled={busy} className={btnGhost}>Choose another file</button>
          </>}
          {step === "review" && <>
            <button onClick={commit} disabled={busy || toSave === 0} className={btnPrimary}><M n="upload" c="text-sm" />{busy ? "Importing…" : `Import ${toSave} ${toSave === 1 ? one : many}`}</button>
            <button onClick={() => setStep("columns")} disabled={busy} className={btnGhost}>Back to columns</button>
            {counts.error > 0 && toSave > 0 && <span className="text-[11px] text-ink-tertiary">Rows to fix are skipped.</span>}
          </>}
          {step === "done" && <>
            <button onClick={onClose} className={btnPrimary}>Done</button>
            {result?.batch_id && <button onClick={() => undo(result.batch_id!)} disabled={busy} className={btnGhost}><M n="undo" c="text-sm" />Undo import</button>}
          </>}
          {step === "start" && <button onClick={onClose} className={btnGhost}>Cancel</button>}
        </div>
      </div>
    </div>
  );
}
