import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { money } from "../orders/types";
import { tint, hueText } from "../../lib/tint";
import { type Member, MemberCard, StatusPill, fmtDate } from "./shared";

// Contacts › Memberships: the list a manager works from. Five filters, one row per plan, and a
// side panel with what's left, the payments and the few changes a plan needs.

type View = "all" | "renewing" | "unpaid" | "paused" | "former";
const VIEWS: { k: View; label: string }[] = [
  { k: "all", label: "All members" }, { k: "renewing", label: "Renewing soon" }, { k: "unpaid", label: "Unpaid" },
  { k: "paused", label: "Paused" }, { k: "former", label: "Former" },
];

export default function MembershipsView({ q = "" }: { q?: string }) {
  const [view, setView] = useState<View>("all");
  const [data, setData] = useState<{ items: Member[]; counts: Record<string, number> } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    const qs = new URLSearchParams({ view, ...(q.trim() ? { q: q.trim() } : {}) });
    api.get<{ items: Member[]; counts: Record<string, number> }>(`/memberships?${qs}`).then(setData).catch(() => setData({ items: [], counts: {} }));
  }, [view, q, n]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {VIEWS.map((v) => {
          const c = data?.counts?.[v.k];
          return (
            <button key={v.k} onClick={() => setView(v.k)} aria-pressed={view === v.k}
              className={`h-9 px-3.5 rounded-full text-xs font-semibold border transition flex items-center gap-1.5 ${view === v.k ? "bg-ink-primary text-white border-ink-primary" : "bg-white text-ink-secondary border-slate-200 hover:border-slate-300"}`}>
              {v.label}{c ? <span className={`tabular-nums ${view === v.k ? "text-white/70" : v.k === "unpaid" || v.k === "paused" ? "text-amber-700" : "text-ink-tertiary"}`}>{c}</span> : null}
            </button>
          );
        })}
        <a href="/catalog?tab=plans" className="ml-auto h-9 px-3 rounded-full text-xs font-semibold text-primary flex items-center gap-1"><M n="workspace_premium" c="text-sm" />Plans</a>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
        {!data && <p className="p-5 text-xs text-ink-tertiary">Loading…</p>}
        {data && data.items.length === 0 && (
          <div className="p-10 text-center">
            <div className="mx-auto h-11 w-11 rounded-2xl grid place-items-center bg-primary/10 text-primary"><M n="workspace_premium" c="text-xl" /></div>
            <div className="mt-3 text-sm font-semibold text-ink-primary">{view === "all" ? "No members yet" : "Nothing here"}</div>
            <div className="mt-1 text-xs text-ink-tertiary">{view === "all" ? "Add a plan from a client, an asset, or at checkout." : "All caught up."}</div>
          </div>
        )}
        {data?.items.map((m) => (
          <button key={m.id} onClick={() => setOpen(m.id)} className="w-full text-left flex items-center gap-3 px-4 py-3 border-t border-slate-100 first:border-t-0 hover:bg-slate-50/70">
            <div className="h-9 w-9 shrink-0 rounded-xl grid place-items-center" style={{ background: tint(m.color), color: hueText(m.color) }}><M n="workspace_premium" c="text-[17px]" /></div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-ink-primary truncate">{m.client}</div>
              <div className="text-xs text-ink-tertiary truncate">{[m.plan, m.asset, m.billing === "monthly" ? "Monthly" : "Yearly"].filter(Boolean).join(" · ")}</div>
            </div>
            <div className="hidden sm:block text-right text-xs text-ink-tertiary">
              {view === "former" ? `Ended ${fmtDate(m.ended_on)}` : m.status === "scheduled" ? ""
                : m.billing === "monthly" && m.renews ? `Next payment ${fmtDate(m.period_end)}` : `${m.renews ? "Renews" : "Ends"} ${fmtDate(m.renews_on)}`}
            </div>
            {m.owed ? <span className="text-xs font-semibold text-amber-700 tabular-nums">{money(m.owed)}</span> : null}
            <StatusPill m={m} />
          </button>
        ))}
      </div>
      {open && <MembershipDrawer id={open} onClose={() => setOpen(null)} onChanged={() => setN((x) => x + 1)} />}
    </div>
  );
}

export function MembershipDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged?: () => void }) {
  const { can } = useMe();
  const [m, setM] = useState<Member | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<Member>(`/memberships/${id}`).then(setM).catch((e) => setMsg(e?.message || "Couldn't load"));
  useEffect(() => { load(); }, [id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const canChange = can("can_change_memberships");

  async function act(fn: () => Promise<any>, ok: string) {
    setBusy(true); setMsg(null);
    try { const r = await fn(); await load(); onChanged?.(); setMsg(typeof r === "string" ? r : ok); }
    catch (e: any) { setMsg(e?.message || "Something went wrong"); }
    finally { setBusy(false); }
  }
  const [asking, setAsking] = useState(false);
  async function cancel(now: boolean) {
    if (!m) return;
    setAsking(false);
    await act(async () => {
      const r = await api.post<any>(`/memberships/${m.id}/cancel`, { when: now ? "now" : "period_end" });
      return now && r.refund_suggested ? `Cancelled. Refund ${money(r.refund_suggested)} from the invoice if the customer asks.` : now ? "Cancelled" : "Won't renew";
    }, "Done");
  }
  async function link() {
    await act(async () => {
      const r = await api.post<{ url: string }>(`/memberships/${id}/pay-link`, {});
      try { await navigator.clipboard.writeText(r.url); } catch { /* ignore */ }
      return "Payment link copied";
    }, "Payment link copied");
  }

  const btn = "h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 disabled:opacity-50";
  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 55 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[520px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90">
        <header className="px-6 py-4 border-b border-slate-200/90 flex items-center gap-2">
          <div className="min-w-0"><div className="text-base font-semibold text-ink-primary truncate">{m?.client || "Membership"}</div>
            {m && <div className="text-xs text-ink-tertiary">Member since {fmtDate(m.starts_on)}</div>}</div>
          <button aria-label="Close" onClick={onClose} className="ml-auto h-8 w-8 rounded-full grid place-items-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </header>
        <div className="flex-1 overflow-y-auto bg-canvas p-6 space-y-4">
          {m && <MemberCard m={m} />}
          {m && (m.paid != null || m.value_given != null) && (
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-2xl bg-white border border-slate-200/90 p-4"><div className="text-[11px] font-bold text-ink-tertiary">Paid</div><div className="text-lg font-semibold tabular-nums">{money(m.paid || 0)}</div></div>
              <div className="rounded-2xl bg-white border border-slate-200/90 p-4"><div className="text-[11px] font-bold text-ink-tertiary">Work given</div><div className="text-lg font-semibold tabular-nums">{money(m.value_given || 0)}</div></div>
            </div>
          )}
          {!!m?.periods?.length && (
            <div className="rounded-2xl bg-white border border-slate-200/90 overflow-hidden">
              <div className="px-4 pt-3 pb-2 text-[11px] font-bold text-ink-tertiary">Payments</div>
              {m.periods.map((p) => (
                <div key={p.id} className="flex items-center gap-3 px-4 py-2.5 border-t border-slate-100 text-xs">
                  <span className="text-ink-secondary">{fmtDate(p.start)} – {fmtDate(p.end)}</span>
                  <span className="ml-auto tabular-nums font-semibold">{money(p.amount)}</span>
                  <span className={`font-semibold ${p.paid ? "text-emerald-700" : "text-amber-700"}`}>{p.paid ? "Paid" : p.order_id ? "On the job" : "Due"}</span>
                </div>
              ))}
            </div>
          )}
          {asking && m && (
            <div className="rounded-2xl bg-white border border-rose-200 p-4 space-y-3">
              <div className="text-sm font-semibold text-ink-primary">Cancel {m.plan}?</div>
              <div className="text-xs text-ink-secondary">Let it run to {fmtDate(m.renews_on)} without renewing, or end it today.
                {m.refund_if_cancelled_now ? ` Ending today suggests a ${money(m.refund_if_cancelled_now)} refund (unused time, less what the plan already gave).` : ""}</div>
              <div className="flex flex-wrap gap-2">
                <button disabled={busy} onClick={() => cancel(false)} className={btn}>Don't renew</button>
                <button disabled={busy} onClick={() => cancel(true)} className="h-9 px-3.5 rounded-xl bg-rose-600 text-white text-xs font-semibold">End today</button>
                <button onClick={() => setAsking(false)} className="h-9 px-3 text-xs font-semibold text-ink-tertiary">Keep it</button>
              </div>
            </div>
          )}
          {msg && <p role="status" className="rounded-lg bg-white border border-slate-200 px-3 py-2 text-xs text-ink-secondary">{msg}</p>}
        </div>
        {m && ["scheduled", "active", "paused"].includes(m.status) && (
          <footer className="border-t border-slate-200/90 p-4 bg-white flex flex-wrap gap-2">
            {m.unpaid && can("can_create_payment_links") && <button disabled={busy} onClick={link} className="h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm">Copy payment link</button>}
            {canChange && <button disabled={busy} className={btn} onClick={() => act(() => api.post(`/memberships/${m.id}/billing`, { billing: m.billing === "monthly" ? "yearly" : "monthly" }), `Switches to ${m.billing === "monthly" ? "yearly" : "monthly"} from the next payment`)}>
              Switch to {m.billing === "monthly" ? "yearly" : "monthly"}</button>}
            {canChange && (!m.renews || m.status === "paused") && <button disabled={busy} className={btn} onClick={() => act(() => api.post(`/memberships/${m.id}/resume`, {}), "Back on")}>Resume</button>}
            {canChange && m.renews && <button disabled={busy} onClick={() => setAsking(true)} className="h-9 px-3.5 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50 ml-auto">Cancel plan</button>}
          </footer>
        )}
      </div>
    </div>
  );
}
