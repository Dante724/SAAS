// More: who's signed in, which Today to show, appearance, changes waiting to sync, sign out.
import { useEffect, useState } from "react";
import { AlertTriangle, Check, CloudOff, ExternalLink, LogOut, Monitor, Moon, RefreshCw, Sun } from "lucide-react";
import { useMe } from "../../context/Me";
import { Card, Page, Section } from "../components/ui";
import { goQueue, type Conflict } from "../lib/offlineQueue";
import { inferRole, ROLE_LABEL, savedRole, saveRole, type GoRole } from "../lib/role";
import { useQueueStatus } from "../lib/useGo";
import { getTheme, setTheme, type ThemePref } from "../theme";
import { signOut } from "../session";

function Choice<T extends string>({ value, current, onPick, children }: { value: T; current: T; onPick: (v: T) => void; children: React.ReactNode }) {
  const on = value === current;
  return (
    <button onClick={() => onPick(value)} aria-pressed={on}
      className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left text-[1rem] text-ink-primary active:bg-slate-50 first:rounded-t-2xl last:rounded-b-2xl">
      <span className="flex-1 inline-flex items-center gap-3">{children}</span>
      {on && <Check size={20} className="text-primary" />}
    </button>
  );
}

const labelFor = (c: Conflict) => /\/status$/.test(c.url) ? "Status change" : `${c.method} ${c.url.replace(/^\/api\//, "")}`;

export default function More() {
  const { me } = useMe();
  const q = useQueueStatus();
  const [role, setRole] = useState<GoRole | "auto">(savedRole() ?? "auto");
  const [theme, setThemeState] = useState<ThemePref>(getTheme());
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  useEffect(() => { goQueue().conflicts().then(setConflicts).catch(() => {}); }, [q.conflicts]);

  const pickRole = (r: GoRole | "auto") => { setRole(r); saveRole(r === "auto" ? null : r); };
  const pickTheme = (t: ThemePref) => { setThemeState(t); setTheme(t); };

  return (
    <Page title="More">
      <Card className="p-4 mb-6 flex items-center gap-3">
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-white text-[1.125rem] font-bold">
          {(me?.name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase()}
        </span>
        <div className="min-w-0">
          <p className="font-semibold text-ink-primary truncate">{me?.name || "—"}</p>
          <p className="text-[0.875rem] text-ink-secondary truncate">{me?.email}{me?.role ? ` · ${me.role}` : ""}</p>
        </div>
      </Card>

      {(q.pending > 0 || q.conflicts > 0 || !q.online) && (
        <Section title="Sync">
          <Card className="p-4 space-y-3">
            <div className="flex items-center gap-3">
              <CloudOff size={20} className={q.held || !q.online ? "text-amber-700" : "text-ink-tertiary"} />
              <p className="flex-1 text-[0.9375rem] text-ink-primary">
                {!q.online ? "You're offline. " : ""}
                {q.pending ? `${q.pending} change${q.pending > 1 ? "s" : ""} waiting to send.` : "Nothing waiting to send."}
                {q.held && <span className="block text-[0.875rem] text-amber-800">This shop only accepts changes from its own network. They'll send on shop Wi-Fi.</span>}
              </p>
              {q.pending > 0 && q.online && (
                <button onClick={() => goQueue().flush()} aria-label="Send now" className="h-11 w-11 inline-flex items-center justify-center rounded-full text-primary active:bg-slate-100">
                  <RefreshCw size={20} className={q.syncing ? "animate-spin" : ""} />
                </button>
              )}
            </div>
            {conflicts.map((c) => (
              <div key={c.id} className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                <p className="flex items-center gap-2 font-semibold text-amber-800"><AlertTriangle size={16} />{labelFor(c)} didn't go through</p>
                <p className="mt-0.5 text-[0.875rem] text-amber-800">{c.detail}</p>
                <div className="mt-2 flex gap-2">
                  <button onClick={() => goQueue().resolveConflict(c.id, "discard")} className="min-h-[2.75rem] flex-1 rounded-xl border border-amber-300 bg-white font-semibold text-ink-primary">Discard</button>
                  <button onClick={() => goQueue().resolveConflict(c.id, "retry")} className="min-h-[2.75rem] flex-1 rounded-xl bg-primary font-semibold text-white">Try again</button>
                </div>
              </div>
            ))}
          </Card>
        </Section>
      )}

      <Section title="Today shows">
        <Card className="divide-y divide-slate-100">
          <Choice value="auto" current={role} onPick={pickRole}>Automatic <span className="text-ink-tertiary text-[0.875rem]">({ROLE_LABEL[inferRole(me)]})</span></Choice>
          {(["tech", "desk", "owner"] as GoRole[]).map((r) => <Choice key={r} value={r} current={role} onPick={pickRole}>{ROLE_LABEL[r]}</Choice>)}
        </Card>
      </Section>

      <Section title="Appearance">
        <Card className="divide-y divide-slate-100">
          <Choice value="system" current={theme} onPick={pickTheme}><Monitor size={18} className="text-ink-tertiary" />Same as phone</Choice>
          <Choice value="light" current={theme} onPick={pickTheme}><Sun size={18} className="text-ink-tertiary" />Light</Choice>
          <Choice value="dark" current={theme} onPick={pickTheme}><Moon size={18} className="text-ink-tertiary" />Dark</Choice>
        </Card>
      </Section>

      <Card className="divide-y divide-slate-100">
        <a href="/" className="min-h-[3rem] flex items-center gap-3 px-4 text-ink-primary active:bg-slate-50 rounded-t-2xl"><ExternalLink size={18} className="text-ink-tertiary" />Open the full Tekly app</a>
        <button onClick={signOut} className="w-full min-h-[3rem] flex items-center gap-3 px-4 text-left font-semibold text-rose-700 active:bg-slate-50 rounded-b-2xl"><LogOut size={18} />Sign out</button>
      </Card>
    </Page>
  );
}
