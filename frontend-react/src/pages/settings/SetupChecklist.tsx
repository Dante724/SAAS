import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { SETUP, type SectionId, type SetupStep } from "./settingsRegistry";

/* Settings → Overview setup guide. Every step is worked out from the shop's real data, so it
   can't be ticked by hand; steps that don't apply to a shop can be skipped (remembered on this
   device). `load` is injectable so the guide renders in isolation in tests. */

export type SetupState = Record<string, boolean>;

const n = (r: any) => (Array.isArray(r) ? r.length : Array.isArray(r?.items) ? r.items.length : 0);

export async function loadSetupState(): Promise<SetupState> {
  const get = (u: string) => api.get<any>(u).catch(() => null);
  const [co, taxes, team, invites, pay, prov, pages, orders] = await Promise.all([
    get("/staff/company"), get("/catalog/taxes"), get("/staff/employees"), get("/staff/invites"),
    get("/payments/config"), get("/notifications/providers"), get("/staff/public-pages"), get("/orders?limit=1"),
  ]);
  return {
    shop: !!(co?.name && co?.company_phone && (co?.company_address || co?.company_email)),
    tax: n(taxes) > 0,
    team: n(team) > 1 || n(invites) > 0,
    payments: !!pay?.live,
    texts: prov?.channels?.sms === "live" || prov?.channels?.whatsapp === "live",
    booking: !!pages?.appointment_enabled,
    first: n(orders) > 0,
  };
}

const SKIP_KEY = "ros_setup_skipped";
const readSkipped = (): string[] => { try { return JSON.parse(localStorage.getItem(SKIP_KEY) || "[]"); } catch { return []; } };
const writeSkipped = (v: string[]) => { try { localStorage.setItem(SKIP_KEY, JSON.stringify(v)); } catch { /* private mode */ } };

export function SetupChecklist({ goSection, load = loadSetupState, navigate = (to: string) => { window.location.href = to; } }: {
  goSection: (s: SectionId) => void; load?: () => Promise<SetupState>; navigate?: (to: string) => void;
}) {
  const [state, setState] = useState<SetupState | null>(null);
  const [skipped, setSkipped] = useState<string[]>(readSkipped);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { let on = true; load().then((s) => on && setState(s)).catch(() => on && setState({})); return () => { on = false; }; }, [load]);

  const done = (x: SetupStep) => !!state?.[x.id];
  const settled = (x: SetupStep) => done(x) || skipped.includes(x.id);
  const count = SETUP.filter(settled).length;
  const pct = Math.round((count / SETUP.length) * 100);
  const left = SETUP.filter((x) => !settled(x));
  const minutes = left.reduce((a, x) => a + x.min, 0);
  const next = left[0];
  const open = (x: SetupStep) => (x.go.startsWith("/") ? navigate(x.go) : goSection(x.go as SectionId));
  const skip = (id: string) => { const v = [...skipped, id]; setSkipped(v); writeSkipped(v); };
  const unskip = (id: string) => { const v = skipped.filter((s) => s !== id); setSkipped(v); writeSkipped(v); };
  const rows = showAll || !left.length ? SETUP : SETUP.filter((x) => !settled(x) || x === next);

  const R = 26, C = 2 * Math.PI * R;
  return (
    <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden" aria-label="Setup guide">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-5 pt-5 pb-4">
        <svg width="64" height="64" viewBox="0 0 64 64" className="flex-shrink-0" aria-hidden>
          <circle cx="32" cy="32" r={R} fill="none" stroke="#e2e8f0" strokeWidth="6" />
          <circle cx="32" cy="32" r={R} fill="none" stroke="currentColor" strokeWidth="6" strokeLinecap="round" className="text-primary transition-all duration-700"
            strokeDasharray={C} strokeDashoffset={state ? C * (1 - pct / 100) : C} transform="rotate(-90 32 32)" />
          <text x="32" y="36" textAnchor="middle" className="fill-current text-ink-primary" fontSize="13" fontWeight="600">{state ? `${pct}%` : ""}</text>
        </svg>
        <div className="min-w-0 flex-1 basis-40">
          <h3 className="text-base font-semibold text-ink-primary">{!state ? "Checking your setup…" : left.length ? "Get ready for your first customers" : "You're all set"}</h3>
          <p className="text-xs text-ink-tertiary">{!state ? "" : left.length ? `${count} of ${SETUP.length} done · about ${minutes} minutes left` : "Everything's in place. You can change any of it below."}</p>
          {state && next && (
            <button onClick={() => open(next)} className="mt-3 h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:opacity-90">{next.go.startsWith("/") ? "Start" : "Continue"}: {next.t.split(" ").slice(0, 3).join(" ")}</button>
          )}
        </div>
      </div>
      {state && (
        <div className="px-2 pb-2">
          {rows.map((x) => {
            const isDone = done(x); const isSkipped = !isDone && skipped.includes(x.id);
            return (
              <div key={x.id} className={`group flex items-center gap-3 px-3 py-2.5 rounded-xl ${x === next ? "bg-primary/5" : "hover:bg-slate-50"}`}>
                <span aria-label={isDone ? "Done" : isSkipped ? "Skipped" : "To do"} className={`w-6 h-6 rounded-full grid place-items-center flex-shrink-0 ${isDone ? "bg-emerald-500 text-white" : isSkipped ? "bg-slate-200 text-ink-tertiary" : "border-2 border-slate-300"}`}>
                  {isDone && <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth={3}><path d="M5 12l4 4L19 6" /></svg>}
                  {isSkipped && <span className="text-[11px] leading-none">–</span>}
                </span>
                <span className="min-w-0 flex-1">
                  <span className={`block text-sm ${isDone || isSkipped ? "text-ink-tertiary" : "text-ink-primary"}`}>{x.t}</span>
                  <span className="block text-xs text-ink-tertiary">{isSkipped ? "Skipped" : x.d}{!isDone && !isSkipped ? ` · ${x.min} min` : ""}</span>
                </span>
                {!isDone && !isSkipped && x.skippable && <button onClick={() => skip(x.id)} className="px-2 py-1 rounded-lg text-[11px] text-ink-tertiary hover:text-ink-secondary opacity-0 group-hover:opacity-100 focus:opacity-100">Skip</button>}
                {isSkipped && <button onClick={() => unskip(x.id)} className="px-2 py-1 rounded-lg text-[11px] text-ink-tertiary hover:text-ink-secondary">Undo</button>}
                {!isDone && <button onClick={() => open(x)} aria-label={`Open: ${x.t}`} className="px-3 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10">{isSkipped ? "Open" : "Set up"}</button>}
              </div>
            );
          })}
          {left.length > 0 && count > 0 && (
            <button onClick={() => setShowAll((v) => !v)} className="w-full text-center py-2 text-[11px] font-semibold text-ink-tertiary hover:text-ink-secondary">{showAll ? "Hide finished steps" : `Show ${count} finished`}</button>
          )}
        </div>
      )}
    </section>
  );
}
