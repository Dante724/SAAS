import { useEffect, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import Switch from "../../components/Switch";
import { SettingRow, NumField } from "../../components/SettingRow";
import { loadFailed } from "../../lib/notice";
import SettingsPage from "../../components/SettingsPage";
import Section from "../../components/Section";
import Button, { buttonClass } from "../../components/Button";
import Tabs from "../../components/Tabs";
import { fieldClass, labelClass } from "../../components/Field";
import { settingsToast, rowField } from "./kit";
import { laborText } from "../../lib/labor";

/* Settings › Public pages — the three pages customers see (booking, job tracking, estimate
   approval), one brand colour for all of them, and a live phone preview. Same
   /staff/public-pages endpoint; each change saves on its own like before. Settings the public
   pages don't read yet (separate tables, zero-price lines, background pattern…) are left out
   rather than shown as switches that do nothing. Every change here is a single choice, so it
   saves the moment it's made and confirms with the page toast. */

type Status = { key: string; name: string; status_group?: string; color?: string };
type Picker = (p: { value: string; list: Status[]; onChange: (v: string) => void }) => ReactNode;

const ACCENTS = ["#3b6fe0", "#185FA5", "#7F77DD", "#1D9E75", "#0F6E56", "#D85A30", "#D4537E", "#2C2C2A"];
const SLOTS: [number, string][] = [[15, "15 minutes"], [30, "30 minutes"], [45, "45 minutes"], [60, "1 hour"], [90, "1.5 hours"], [120, "2 hours"]];
const LEADS: [number, string][] = [[0, "Any time"], [60, "1 hour"], [120, "2 hours"], [240, "4 hours"], [1440, "1 day"], [2880, "2 days"]];
const WINDOWS: [number, string][] = [[7, "1 week"], [14, "2 weeks"], [30, "1 month"], [60, "2 months"], [90, "3 months"]];
const inp = fieldClass();

// A settings card. Defined here (not inside the page) so typing in it doesn't remount it.
// `control` is the card's own on/off switch: the first row of the card, label left, switch right.
function Card({ icon, title, hint, control, children, onEnter }: { icon: string; title: string; hint?: string; control?: { label: string; node: ReactNode }; children?: ReactNode; onEnter?: () => void }) {
  return (
    <div onMouseEnter={onEnter}>
      <Section icon={icon} title={title} description={hint}>
        {control && <div className={children ? "pb-3 mb-1 border-b border-slate-100" : ""}><SettingRow label={control.label}>{control.node}</SettingRow></div>}
        {children}
      </Section>
    </div>
  );
}

export default function PublicPagesSettings({ shopName, logo, StatusPicker, after }: { shopName: string; logo?: string | null; StatusPicker: Picker;
  /** More cards at the end of the column (the hub passes Self check-in). */
  after?: ReactNode }) {
  const [cfg, setCfg] = useState<any>(null);
  const [slug, setSlug] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState<"booking" | "tracking" | "estimate">("booking");
  const [est, setEst] = useState<Status[]>([]);
  const [wo, setWo] = useState<Status[]>([]);
  const flash = (m: string) => settingsToast(m);
  const load = () => api.get<any>("/staff/public-pages").then((c) => { setCfg(c); setSlug(c.slug || ""); }).catch(() => setCfg({}));
  useEffect(() => {
    load();
    api.get<any>("/statuses?record_type=estimate").then((r) => setEst(r.items || [])).catch(loadFailed("statuses"));
    api.get<any>("/statuses?record_type=work_order").then((r) => setWo(r.items || [])).catch(loadFailed("statuses"));
  }, []);
  const head = { title: "Public pages", sub: "The pages your customers open from a link: booking a visit, tracking a repair, and approving an estimate." };
  if (!cfg) return <SettingsPage wide {...head}><div className="h-64" aria-busy="true" /></SettingsPage>;

  const url = `${location.origin}/book/${cfg.slug}`;
  const accent = cfg.page_color || "#3b6fe0";
  async function save(patch: any, msg = "Saved") {
    setCfg((c: any) => ({ ...c, ...patch }));
    try { await api.put("/staff/public-pages", patch); setErr(null); flash(msg); } catch (e: any) { setErr(e?.message || "Couldn't save"); load(); }
  }
  async function saveSlug() {
    const s = slug.trim().toLowerCase();
    if (!s || s === cfg.slug) return;
    try { await api.put("/staff/public-pages/subdomain", { subdomain: s }); flash("Link updated"); load(); }
    catch (e: any) { setErr(e?.message || "Couldn't change the link"); }
  }

  // Plain functions (not components), so rows aren't remounted on every change.
  const sw = (k: string, label: string, hint?: string, onFocus?: typeof page) => (
    <div key={k} className="py-3 border-t border-slate-100 first:border-0 first:pt-0" onMouseEnter={() => onFocus && setPage(onFocus)}>
      <SettingRow label={label} help={hint}><Switch label={label} on={!!cfg[k]} onChange={(v) => save({ [k]: v })} /></SettingRow>
    </div>
  );
  const pick = (k: string, label: string, opts: [number, string][], hint?: string) => {
    const v = cfg[k] ?? opts[0][0];
    const known = opts.some(([x]) => x === v);
    return (
      <label className="block">
        <span className={labelClass}>{label}</span>
        <select value={v} onChange={(e) => save({ [k]: Number(e.target.value) })} className={inp}>
          {!known && <option value={v}>{v}</option>}
          {opts.map(([x, l]) => <option key={x} value={x}>{l}</option>)}
        </select>
        {hint && <span className="block text-xs text-ink-tertiary mt-1">{hint}</span>}
      </label>
    );
  };
  const bigSwitch = (k: string, label: string) => ({ label, node: <Switch label={label} on={!!cfg[k]} onChange={(v) => save({ [k]: v })} /> });
  const statusSelect = (k: string, list: Status[], label: string) => (
    <label className="block">
      <span className={labelClass}>{label}</span>
      <select value={cfg[k] || ""} onChange={(e) => save({ [k]: e.target.value })} className={inp}>
        <option value="">Keep the status</option>
        {list.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
      </select>
    </label>
  );
  const approval = (prefix: "oe" | "oe_wo", list: Status[], noun: string) => {
    const on = prefix === "oe" ? "oe_allow_approval" : "oe_wo_allow_approval";
    const sig = prefix === "oe" ? "oe_estimate_require_signature" : "oe_wo_require_signature";
    const sts = prefix === "oe" ? "oe_approval_statuses" : "oe_wo_approval_statuses";
    const acc = prefix === "oe" ? "oe_estimate_accept_status" : "oe_wo_accept_status";
    const dec = prefix === "oe" ? "oe_estimate_decline_status" : "oe_wo_decline_status";
    return (
      <Card icon={prefix === "oe" ? "request_quote" : "assignment_late"} title={`Customers approve ${noun}s`} hint={`Adds Approve and Decline buttons to the ${noun} link.`}
        onEnter={() => setPage("estimate")} control={bigSwitch(on, `Customers approve ${noun}s`)}>
        {cfg[on] && (
          <div className="space-y-4">
            {sw(sig, "Ask for a signature", "They sign with a finger or mouse before approving.", "estimate")}
            {prefix === "oe" && sw("oe_estimate_line_choice", "Let them leave lines out", "They can untick lines they don't want before approving. You see what they left out on the job.", "estimate")}
            <div>
              <span className={labelClass}>Only while the {noun} is in</span>
              {StatusPicker({ value: cfg[sts] || "", list, onChange: (v) => save({ [sts]: v }) })}
              <span className="block text-xs text-ink-tertiary mt-1">Leave empty to allow it in any status.</span>
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              {statusSelect(acc, list, "When approved, move to")}
              {statusSelect(dec, list, "When declined, move to")}
            </div>
          </div>
        )}
      </Card>
    );
  };

  const name = shopName || "Your shop";
  const logoEl = (show: boolean) => show && logo
    ? <img src={logo} alt="" className="w-8 h-8 rounded-lg object-contain bg-white" />
    : <span className="w-8 h-8 rounded-lg grid place-items-center text-xs font-semibold text-white" style={{ background: accent }}>{name.slice(0, 1).toUpperCase()}</span>;

  return (
    <SettingsPage wide {...head}>
      {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

      <div className="grid xl:grid-cols-[minmax(0,1fr)_300px] gap-4 items-start">
        <div className="space-y-4 min-w-0">
          <Card icon="auto_awesome" title="Your brand on every page" hint="Name and logo come from General. Pick the colour for buttons and highlights.">
            <div className="flex items-center gap-2 flex-wrap">
              {ACCENTS.map((c) => (
                <button key={c} type="button" onClick={() => save({ page_color: c })} aria-label={`Colour ${c}`}
                  className={`w-8 h-8 rounded-full grid place-items-center transition-transform hover:scale-110 ${accent.toLowerCase() === c.toLowerCase() ? "ring-2 ring-offset-2 ring-slate-400" : ""}`} style={{ background: c }}>
                  {accent.toLowerCase() === c.toLowerCase() && <M n="check" c="text-sm text-white" />}
                </button>
              ))}
              <label className="w-8 h-8 rounded-full border border-dashed border-slate-300 grid place-items-center cursor-pointer text-ink-tertiary hover:border-primary hover:text-primary" title="Custom colour">
                <M n="colorize" c="text-sm" /><input type="color" value={accent} onChange={(e) => setCfg({ ...cfg, page_color: e.target.value })} onBlur={() => save({ page_color: cfg.page_color })} className="sr-only" />
              </label>
            </div>
            <div className="mt-3 pt-3 border-t border-slate-100">
              <SettingRow label="Show your logo" help="On every customer page.">
                <Switch label="Show logo on customer pages" on={!!cfg.page_show_logo} onChange={(v) => save({ page_show_logo: v, oe_show_logo: v })} />
              </SettingRow>
            </div>
          </Card>

          <Card icon="calendar_month" title="Online booking" hint="Customers pick a time and tell you what's wrong." onEnter={() => setPage("booking")}
            control={bigSwitch("appointment_enabled", "Online booking")}>
            <div className={cfg.appointment_enabled ? "" : "opacity-60"}>
              <div className="flex flex-wrap sm:flex-nowrap items-center gap-2 mb-4">
                <div className="glass-input h-9 flex-1 min-w-0 basis-full sm:basis-auto flex items-center rounded-xl overflow-hidden">
                  <span className="pl-3 text-xs text-ink-tertiary whitespace-nowrap">{location.host}/book/</span>
                  <input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} onBlur={saveSlug} onKeyDown={(e) => e.key === "Enter" && saveSlug()}
                    aria-label="Booking link name" className="flex-1 min-w-0 bg-transparent pr-3 text-sm font-medium text-ink-primary outline-none" />
                </div>
                <Button onClick={() => { navigator.clipboard?.writeText(url); flash("Link copied"); }} icon={<M n="content_copy" c="text-sm" />}>Copy</Button>
                <a href={url} target="_blank" rel="noreferrer" className={buttonClass("secondary")}><M n="open_in_new" c="text-sm" />Open</a>
              </div>
              <div className="grid sm:grid-cols-3 gap-3 mb-2">
                {pick("appointment_time_slot", "Visit length", SLOTS)}
                {pick("appointment_lead_min", "Earliest booking", LEADS, "How soon from now")}
                {pick("appointment_window_days", "Book up to", WINDOWS, "How far ahead")}
              </div>
              {sw("book_show_employee_step", "Let customers choose a technician", "Off: you assign someone later.", "booking")}
              <div className="py-3 border-t border-slate-100" onMouseEnter={() => setPage("booking")}>
                <SettingRow stack htmlFor="pp-names" label="How your team's names appear" help="On the booking page and in customer messages. Custom uses each person's “Name on documents” in Team.">
                <select id="pp-names" value={cfg.public_employee_name || "full"} onChange={(e) => save({ public_employee_name: e.target.value })} aria-label="How your team's names appear"
                  className={fieldClass(`${rowField} sm:w-56 pr-8`)}>
                  <option value="full">Full name</option>
                  <option value="first_initial">First name and initial</option>
                  <option value="first">First name only</option>
                  <option value="custom">Custom name</option>
                </select>
                </SettingRow>
              </div>
              {sw("appointment_allow_multiple", "Allow more than one booking at the same time", undefined, "booking")}
              {sw("appointment_consider_orders", "Keep times free when jobs are scheduled", "Scheduled work orders block booking slots too.", "booking")}
            </div>
          </Card>

          <Card icon="visibility" title="Repair tracking page" hint="Every job gets a private link the customer can open any time." onEnter={() => setPage("tracking")}>
            {sw("oe_allow_messages", "Customers can message you", "Each status can allow or block it too (Statuses).", "tracking")}
            {cfg.oe_allow_messages !== false && (
              <div className="py-3 border-t border-slate-100" onBlur={() => save({ oe_msg_days_after_close: Number(cfg.oe_msg_days_after_close) || 0 })}>
                <SettingRow htmlFor="pp-msg-days" label="Keep messages open after a job is finished" help="Customers can still write to you for this long once the job is closed.">
                  <NumField id="pp-msg-days" max={365} value={cfg.oe_msg_days_after_close ?? 14} unit="days"
                    onChange={(v) => setCfg({ ...cfg, oe_msg_days_after_close: v })} />
                </SettingRow>
              </div>
            )}
            {sw("oe_allow_staff_reply", "Your team can reply on the page", undefined, "tracking")}
            {sw("oe_allow_recommend", "Customers can approve extra work you recommend", undefined, "tracking")}
            {sw("oe_show_scheduled", "Show the scheduled date", undefined, "tracking")}
          </Card>

          {approval("oe", est, "estimate")}
          {approval("oe_wo", wo, "work order")}

          <Card icon="draw" title="Drop-off terms" hint="What the customer reads and signs on the tablet when they leave an item (Tekly Go). Leave empty to use the standard wording.">
            <textarea value={cfg.dropoff_terms ?? ""} maxLength={4000} rows={4} aria-label="Drop-off terms"
              onChange={(e) => setCfg({ ...cfg, dropoff_terms: e.target.value })}
              onBlur={() => save({ dropoff_terms: (cfg.dropoff_terms || "").trim() })}
              placeholder="I'm leaving this item for diagnosis and repair. I've backed up my data; the shop isn't responsible for data loss…"
              className={fieldClass("h-auto py-2")} />
          </Card>

          {after}
        </div>

        {/* Live preview */}
        <aside className="xl:sticky xl:top-4" aria-label="Preview">
          <Tabs look="segmented" label="Preview page" value={page} onChange={setPage} className="mb-3"
            tabs={[{ id: "booking", label: "Booking" }, { id: "tracking", label: "Tracking" }, { id: "estimate", label: "Estimate" }]} />
          <div className="always-light rounded-[34px] border-[7px] border-slate-800 bg-white overflow-hidden mx-auto w-[260px] shadow-clay-card">
            <div className="h-[470px] overflow-hidden flex flex-col text-2xs">
              <div className="flex items-center gap-2 px-3 py-3 border-b border-slate-100">
                {logoEl(!!cfg.page_show_logo)}<span className="font-semibold text-ink-primary truncate">{name}</span>
              </div>

              {page === "booking" && (
                !cfg.appointment_enabled ? (
                  <div className="flex-1 grid place-items-center px-6 text-center text-ink-tertiary">Online booking is off. Turn it on to share your booking link.</div>
                ) : (
                  <div className="p-3 space-y-3">
                    <div className="text-sm font-semibold text-ink-primary">Book a visit</div>
                    <div className="space-y-1.5">
                      {["Screen repair", "Battery replacement", "Diagnostics"].map((s, i) => (
                        <div key={s} className="flex items-center justify-between rounded-xl border px-2.5 py-2" style={i === 0 ? { borderColor: accent, background: `${accent}10` } : { borderColor: "#e2e8f0" }}>
                          <span className="text-ink-primary">{s}</span><span className="text-ink-tertiary">{SLOTS.find(([x]) => x === (cfg.appointment_time_slot ?? 30))?.[1] || `${cfg.appointment_time_slot} min`}</span>
                        </div>
                      ))}
                    </div>
                    {cfg.book_show_employee_step && (
                      <div className="flex flex-wrap gap-1.5">{["Any", ...(cfg.public_employee_name === "first" ? ["Priya", "Amit"] : cfg.public_employee_name === "first_initial" ? ["Priya S.", "Amit R."] : cfg.public_employee_name === "custom" ? ["Priya S.", "Amit (lead tech)"] : ["Priya Shah", "Amit Rao"])].map((p, i) => <span key={p} className="px-2.5 py-1 rounded-full border text-2xs" style={i === 0 ? { borderColor: accent, color: accent } : { borderColor: "#e2e8f0" }}>{p}</span>)}</div>
                    )}
                    <div className="grid grid-cols-4 gap-1.5">
                      {["9:00", "9:30", "10:00", "10:30", "11:00", "1:00", "1:30", "2:00"].map((t, i) => (
                        <span key={t} className="text-center rounded-lg py-1.5 border text-2xs" style={i === 2 ? { background: accent, color: "#fff", borderColor: accent } : { borderColor: "#e2e8f0" }}>{t}</span>
                      ))}
                    </div>
                    <div className="rounded-xl py-2.5 text-center text-white font-semibold" style={{ background: accent }}>Book 10:00</div>
                  </div>
                )
              )}

              {page === "tracking" && (
                <div className="p-3 space-y-3 flex-1">
                  <div><div className="text-2xs text-ink-tertiary">iPhone 14 · WO-1042</div><div className="text-sm font-semibold text-ink-primary">In repair</div></div>
                  <div className="flex items-center gap-1">{[0, 1, 2, 3].map((i) => <div key={i} className="flex-1 h-1.5 rounded-full" style={{ background: i <= 1 ? accent : "#e2e8f0" }} />)}</div>
                  {cfg.oe_show_scheduled && <div className="text-ink-secondary">Ready by Fri, Oct 18</div>}
                  {cfg.oe_allow_recommend && (
                    <div className="rounded-xl border border-slate-200 p-2.5">
                      <div className="font-semibold text-ink-primary">We recommend</div>
                      <div className="flex justify-between text-ink-secondary mt-1"><span>Screen protector</span><span>$19</span></div>
                      <div className="mt-2 rounded-lg py-1.5 text-center text-white font-semibold" style={{ background: accent }}>Approve</div>
                    </div>
                  )}
                  {cfg.oe_allow_messages !== false ? (
                    <div className="rounded-xl border border-slate-200 px-2.5 py-2 text-ink-tertiary">Message {name}…</div>
                  ) : <div className="text-ink-tertiary">Messages are turned off.</div>}
                </div>
              )}

              {page === "estimate" && (
                <div className="p-3 space-y-3 flex-1">
                  <div className="text-sm font-semibold text-ink-primary">Estimate EST-208</div>
                  <div className="space-y-1 text-ink-secondary">
                    <div className="flex justify-between"><span>Screen replacement</span><span>$189.00</span></div>
                    <div className="flex justify-between"><span>{laborText("Labour")}</span><span>$40.00</span></div>
                    <div className="flex justify-between font-semibold text-ink-primary pt-1 border-t border-dashed border-slate-200"><span>Total</span><span>$229.00</span></div>
                  </div>
                  {cfg.oe_allow_approval ? (<>
                    {cfg.oe_estimate_require_signature && <div className="h-12 rounded-xl border border-dashed border-slate-300 grid place-items-center text-ink-tertiary">Sign here</div>}
                    <div className="grid grid-cols-2 gap-1.5">
                      <div className="rounded-xl py-2 text-center border border-slate-200 text-ink-secondary">Decline</div>
                      <div className="rounded-xl py-2 text-center text-white font-semibold" style={{ background: accent }}>Approve</div>
                    </div>
                  </>) : <div className="text-ink-tertiary">Customers can view it. Approving happens in the shop.</div>}
                </div>
              )}
            </div>
          </div>
          <p className="text-2xs text-ink-tertiary text-center mt-2">Sample job and customer.</p>
        </aside>
      </div>
    </SettingsPage>
  );
}
