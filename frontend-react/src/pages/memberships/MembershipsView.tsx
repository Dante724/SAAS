import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { money } from "../orders/types";
import { tint, hueText } from "../../lib/tint";
import { type Member, MemberCard, StatusPill, fmtDate } from "./shared";
import PageSummary from "../../components/PageSummary";
import { Avatar } from "../orders/look";

// Contacts › Memberships: the list a manager works from. Five filters, one row per plan, and a
// side panel with what's left, the payments and the few changes a plan needs.

type View = "all" | "renewing" | "unpaid" | "paused" | "former";
// One filter row; the numbers above are a summary only, so there's one place to filter.
const VIEWS: { k: View; label: string }[] = [
  { k: "all", label: "All" }, { k: "renewing", label: "Renewing soon" }, { k: "unpaid", label: "Unpaid" },
  { k: "paused", label: "Paused" }, { k: "former", label: "Former" },
];

export default function MembershipsView({ q = "" }: { q?: string }) {
  const [view, setView] = useState<View>("all");
  type Totals = { mrr: number; renewing_value: number; owed: number } | null;
  const [data, setData] = useState<{ items: Member[]; counts: Record<string, number>; totals?: Totals } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    const qs = new URLSearchParams({ view, ...(q.trim() ? { q: q.trim() } : {}) });
    api.get<{ items: Member[]; counts: Record<string, number>; totals?: Totals }>(`/memberships?${qs}`).then(setData).catch(() => setData({ items: [], counts: {} }));
  }, [view, q, n]);

  const c = data?.counts || {};
  const t = data?.totals;
  return (
    <div className="space-y-4">
      {data && (
        <PageSummary stats={[
          { label: "Active members", value: c.all ?? 0, hint: c.paused ? `${c.paused} paused` : "on a plan today" },
          ...(t ? [{ label: "Monthly recurring", value: money(Math.round(t.mrr)), hint: "monthly and yearly plans" }] : []),
          { label: "Renewing in 30 days", value: t ? money(Math.round(t.renewing_value)) : (c.renewing ?? 0), zero: !c.renewing, hint: c.renewing ? `${c.renewing} member${c.renewing === 1 ? "" : "s"}` : "none this month" },
          { label: "Unpaid", value: t ? money(t.owed) : (c.unpaid ?? 0), hint: `${c.unpaid ?? 0} member${c.unpaid === 1 ? "" : "s"}`, tone: "bad" as const, zero: !(c.unpaid) },
        ]} />
      )}
      <div className="flex flex-wrap items-center gap-3">
        <div role="group" aria-label="Show" className="inline-flex flex-wrap gap-0.5 rounded-xl bg-slate-100/80 p-1">
          {VIEWS.map((v) => {
            const n = data?.counts?.[v.k];
            const on = view === v.k;
            return (
              <button key={v.k} onClick={() => setView(v.k)} aria-pressed={on}
                className={`h-8 px-3 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 ${on ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>
                {v.label}{n ? <span className={`tabular-nums ${(v.k === "unpaid" || v.k === "paused") ? "text-amber-700" : "text-ink-tertiary"}`}>{n}</span> : null}
              </button>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
        {!data && <p className="p-5 text-xs text-ink-tertiary">Loading…</p>}
        {data && data.items.length === 0 && (
          <div className="p-10 text-center">
            <div className="mx-auto h-11 w-11 rounded-2xl grid place-items-center bg-primary/10 text-primary"><M n="workspace_premium" c="text-xl" /></div>
            <div className="mt-3 text-sm font-semibold text-ink-primary">{view === "all" ? "No members yet" : "Nothing here"}</div>
            <div className="mt-1 text-xs text-ink-tertiary">{view === "all" ? "Sell a plan from a customer's page, one of their assets, or at checkout." : "All caught up."}</div>
            {view === "all" && <a href="/catalog?tab=plans" className="mt-4 inline-flex h-9 px-3.5 rounded-xl bg-ink-primary text-white text-xs font-semibold items-center">Set up plans</a>}
          </div>
        )}
        {data?.items.map((m) => (
          <button key={m.id} onClick={() => setOpen(m.id)} className="w-full text-left flex items-center gap-3 px-4 py-3 border-t border-slate-100 first:border-t-0 hover:bg-slate-50/70">
            <Avatar name={m.client} size={36} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm font-semibold text-ink-primary truncate">{m.client}</span>
                <span className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-px text-[11px] font-semibold" style={{ background: tint(m.color, 12), color: hueText(m.color) }}>
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: m.color }} />{m.plan}</span>
              </div>
              <div className="text-xs text-ink-tertiary truncate">{[m.asset, m.billing === "monthly" ? "Monthly" : "Yearly"].filter(Boolean).join(" · ")}</div>
              {m.attention && <div className="mt-0.5 inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700"><M n="schedule" c="text-[12px]" />{m.attention.text}</div>}
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
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" aria-hidden="true" onClick={onClose} />
      <div className="relative h-full w-full max-w-[520px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90">
        <header className="px-6 py-4 border-b border-slate-200/90 flex items-center gap-2">
          {m && <Avatar name={m.client} size={44} />}
          <div className="min-w-0"><div className="text-base font-semibold text-ink-primary truncate">{m?.client || "Membership"}</div>
            {m && <div className="text-xs text-ink-tertiary">Member since {fmtDate(m.starts_on)}</div>}</div>
          <button aria-label="Close" onClick={onClose} className="ml-auto h-8 w-8 rounded-full grid place-items-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </header>
        <div className="flex-1 overflow-y-auto bg-canvas p-6 space-y-4">
          {m && <MemberCard m={m} />}
          {m && (m.paid != null || m.value_given != null) && (
            <PageSummary stats={[
              { label: "Paid so far", value: money(m.paid || 0), hint: m.owed ? `${money(m.owed)} due now` : "all settled" },
              { label: "Saved so far", value: money(m.value_given || 0), tone: "good", zero: !m.value_given,
                hint: (m.paid || 0) > 0 && (m.value_given || 0) > (m.paid || 0) ? "more than they've paid" : "work covered by the plan" },
            ]} />
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
