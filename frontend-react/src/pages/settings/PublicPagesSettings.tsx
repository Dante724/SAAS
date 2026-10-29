import { useEffect, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

/* Settings › Public pages — the three pages customers see (booking, job tracking, estimate
   approval), one brand colour for all of them, and a live phone preview. Same
   /staff/public-pages endpoint; each change saves on its own like before. Settings the public
   pages don't read yet (separate tables, zero-price lines, background pattern…) are left out
   rather than shown as switches that do nothing. */

type Status = { key: string; name: string; status_group?: string; color?: string };
type Picker = (p: { value: string; list: Status[]; onChange: (v: string) => void }) => ReactNode;

const ACCENTS = ["#3b6fe0", "#185FA5", "#7F77DD", "#1D9E75", "#0F6E56", "#D85A30", "#D4537E", "#2C2C2A"];
const SLOTS: [number, string][] = [[15, "15 minutes"], [30, "30 minutes"], [45, "45 minutes"], [60, "1 hour"], [90, "1.5 hours"], [120, "2 hours"]];
const LEADS: [number, string][] = [[0, "Any time"], [60, "1 hour"], [120, "2 hours"], [240, "4 hours"], [1440, "1 day"], [2880, "2 days"]];
const WINDOWS: [number, string][] = [[7, "1 week"], [14, "2 weeks"], [30, "1 month"], [60, "2 months"], [90, "3 months"]];

export default function PublicPagesSettings({ shopName, logo, StatusPicker }: { shopName: string; logo?: string | null; StatusPicker: Picker }) {
  const [cfg, setCfg] = useState<any>(null);
  const [slug, setSlug] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState<"booking" | "tracking" | "estimate">("booking");
  const [est, setEst] = useState<Status[]>([]);
  const [wo, setWo] = useState<Status[]>([]);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 1800); };
  const load = () => api.get<any>("/staff/public-pages").then((c) => { setCfg(c); setSlug(c.slug || ""); }).catch(() => setCfg({}));
  useEffect(() => {
    load();
    api.get<any>("/statuses?record_type=estimate").then((r) => setEst(r.items || [])).catch(() => {});
    api.get<any>("/statuses?record_type=work_order").then((r) => setWo(r.items || [])).catch(() => {});
  }, []);
  if (!cfg) return <div className="max-w-3xl text-xs text-ink-tertiary">Loading…</div>;

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

  const Sw = ({ k, label, hint, onFocus }: { k: string; label: string; hint?: string; onFocus?: typeof page }) => (
    <label className="flex items-center gap-3 py-3 border-t border-slate-100 first:border-0 cursor-pointer" onMouseEnter={() => onFocus && setPage(onFocus)}>
      <span className="min-w-0"><span className="block text-sm text-ink-primary">{label}</span>{hint && <span className="block text-[11px] text-ink-tertiary">{hint}</span>}</span>
      <button type="button" role="switch" aria-checked={!!cfg[k]} aria-label={label} onClick={() => save({ [k]: !cfg[k] })}
        className={`ml-auto w-10 h-6 rounded-full flex-shrink-0 relative transition-colors ${cfg[k] ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${cfg[k] ? "left-[18px]" : "left-0.5"}`} /></button>
    </label>
  );
  const Pick = ({ k, label, opts, hint }: { k: string; label: string; opts: [number, string][]; hint?: string }) => {
    const v = cfg[k] ?? opts[0][0];
    const known = opts.some(([x]) => x === v);
    return (
      <label className="block">
        <span className="block text-xs font-medium text-ink-secondary mb-1.5">{label}</span>
        <select value={v} onChange={(e) => save({ [k]: Number(e.target.value) })} className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-sm outline-none focus:border-primary">
          {!known && <option value={v}>{v}</option>}
          {opts.map(([x, l]) => <option key={x} value={x}>{l}</option>)}
        </select>
        {hint && <span className="block text-[11px] text-ink-tertiary mt-1">{hint}</span>}
      </label>
    );
  };
  const Card = ({ icon, title, hint, right, children, focus }: { icon: string; title: string; hint?: string; right?: ReactNode; children?: ReactNode; focus?: typeof page }) => (
    <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card" onMouseEnter={() => focus && setPage(focus)}>
      <div className="flex items-start gap-3 px-5 pt-4 pb-3">
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n={icon} c="text-lg" /></span>
        <div className="min-w-0"><h3 className="text-sm font-semibold text-ink-primary">{title}</h3>{hint && <p className="text-[11px] text-ink-tertiary">{hint}</p>}</div>
        {right && <div className="ml-auto flex-shrink-0">{right}</div>}
      </div>
      {children && <div className="px-5 pb-4">{children}</div>}
    </section>
  );
  const BigSwitch = ({ k, label }: { k: string; label: string }) => (
    <button type="button" role="switch" aria-checked={!!cfg[k]} aria-label={label} onClick={() => save({ [k]: !cfg[k] })}
      className={`w-11 h-6 rounded-full relative transition-colors ${cfg[k] ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${cfg[k] ? "left-[22px]" : "left-0.5"}`} /></button>
  );
  const StatusSelect = ({ k, list, label }: { k: string; list: Status[]; label: string }) => (
    <label className="block">
      <span className="block text-xs font-medium text-ink-secondary mb-1.5">{label}</span>
      <select value={cfg[k] || ""} onChange={(e) => save({ [k]: e.target.value })} className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-sm outline-none focus:border-primary">
        <option value="">Keep the status</option>
        {list.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
      </select>
    </label>
  );
  const Approval = ({ prefix, list, noun }: { prefix: "oe" | "oe_wo"; list: Status[]; noun: string }) => {
    const on = prefix === "oe" ? "oe_allow_approval" : "oe_wo_allow_approval";
    const sig = prefix === "oe" ? "oe_estimate_require_signature" : "oe_wo_require_signature";
    const sts = prefix === "oe" ? "oe_approval_statuses" : "oe_wo_approval_statuses";
    const acc = prefix === "oe" ? "oe_estimate_accept_status" : "oe_wo_accept_status";
    const dec = prefix === "oe" ? "oe_estimate_decline_status" : "oe_wo_decline_status";
    return (
      <Card icon={prefix === "oe" ? "request_quote" : "assignment_late"} title={`Customers approve ${noun}s`} hint={`Adds Approve and Decline buttons to the ${noun} link.`}
        focus="estimate" right={<BigSwitch k={on} label={`Customers approve ${noun}s`} />}>
        {cfg[on] && (
          <div className="space-y-4">
            <Sw k={sig} label="Ask for a signature" hint="They sign with a finger or mouse before approving." onFocus="estimate" />
            <div>
              <span className="block text-xs font-medium text-ink-secondary mb-1.5">Only while the {noun} is in</span>
              {StatusPicker({ value: cfg[sts] || "", list, onChange: (v) => save({ [sts]: v }) })}
              <span className="block text-[11px] text-ink-tertiary mt-1">Leave empty to allow it in any status.</span>
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              <StatusSelect k={acc} list={list} label="When approved, move to" />
              <StatusSelect k={dec} list={list} label="When declined, move to" />
            </div>
          </div>
        )}
      </Card>
    );
  };

  const name = shopName || "Your shop";
  const Logo = ({ show }: { show: boolean }) => show && logo
    ? <img src={logo} alt="" className="w-8 h-8 rounded-lg object-contain bg-white" />
    : <span className="w-8 h-8 rounded-lg grid place-items-center text-xs font-semibold text-white" style={{ background: accent }}>{name.slice(0, 1).toUpperCase()}</span>;

  return (
    <div className="max-w-5xl pb-10">
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
      <div className="mb-5">
        <h2 className="text-base font-semibold text-ink-primary">Customer pages</h2>
        <p className="text-xs text-ink-tertiary">The pages your customers open from a link: booking a visit, tracking a repair, and approving an estimate.</p>
      </div>
      {err && <div className="mb-4 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

      <div className="grid xl:grid-cols-[minmax(0,1fr)_300px] gap-5 items-start">
        <div className="space-y-5 min-w-0">
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
              <span className="ml-auto flex items-center gap-2 text-xs text-ink-secondary">Show logo
                <button type="button" role="switch" aria-checked={!!cfg.page_show_logo} aria-label="Show logo on customer pages" onClick={() => save({ page_show_logo: !cfg.page_show_logo, oe_show_logo: !cfg.page_show_logo })}
                  className={`w-10 h-6 rounded-full relative transition-colors ${cfg.page_show_logo ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${cfg.page_show_logo ? "left-[18px]" : "left-0.5"}`} /></button>
              </span>
            </div>
          </Card>

          <Card icon="calendar_month" title="Online booking" hint="Customers pick a time and tell you what's wrong." focus="booking"
            right={<BigSwitch k="appointment_enabled" label="Online booking" />}>
            <div className={cfg.appointment_enabled ? "" : "opacity-60"}>
              <div className="flex items-center gap-2 mb-4">
                <div className="flex-1 min-w-0 flex items-center bg-slate-50 border border-slate-200 rounded-xl overflow-hidden">
                  <span className="pl-3 text-xs text-ink-tertiary whitespace-nowrap">{location.host}/book/</span>
                  <input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} onBlur={saveSlug} onKeyDown={(e) => e.key === "Enter" && saveSlug()}
                    aria-label="Booking link name" className="flex-1 min-w-0 bg-transparent py-2.5 pr-3 text-sm font-medium text-ink-primary outline-none" />
                </div>
                <button onClick={() => { navigator.clipboard?.writeText(url); flash("Link copied"); }} className="px-3 py-2.5 rounded-xl border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50 inline-flex items-center gap-1"><M n="content_copy" c="text-sm" />Copy</button>
                <a href={url} target="_blank" rel="noreferrer" className="px-3 py-2.5 rounded-xl border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50 inline-flex items-center gap-1"><M n="open_in_new" c="text-sm" />Open</a>
              </div>
              <div className="grid sm:grid-cols-3 gap-3 mb-2">
                <Pick k="appointment_time_slot" label="Visit length" opts={SLOTS} />
                <Pick k="appointment_lead_min" label="Earliest booking" opts={LEADS} hint="How soon from now" />
                <Pick k="appointment_window_days" label="Book up to" opts={WINDOWS} hint="How far ahead" />
              </div>
              <Sw k="book_show_employee_step" label="Let customers choose a technician" hint="Off: you assign someone later." onFocus="booking" />
              <label className="flex flex-wrap items-center gap-3 py-3 border-t border-slate-100" onMouseEnter={() => setPage("booking")}>
                <span className="min-w-0"><span className="block text-sm text-ink-primary">How your team's names appear</span><span className="block text-[11px] text-ink-tertiary">On the booking page and in customer messages. Custom uses each person's “Name on documents” in Team.</span></span>
                <select value={cfg.public_employee_name || "full"} onChange={(e) => save({ public_employee_name: e.target.value })} aria-label="How your team's names appear"
                  className="ml-auto bg-white border border-slate-200 rounded-xl px-3 py-2 text-sm outline-none focus:border-primary">
                  <option value="full">Full name</option>
                  <option value="first_initial">First name and initial</option>
                  <option value="first">First name only</option>
                  <option value="custom">Custom name</option>
                </select>
              </label>
              <Sw k="appointment_allow_multiple" label="Allow more than one booking at the same time" onFocus="booking" />
              <Sw k="appointment_consider_orders" label="Keep times free when jobs are scheduled" hint="Scheduled work orders block booking slots too." onFocus="booking" />
            </div>
          </Card>

          <Card icon="visibility" title="Repair tracking page" hint="Every job gets a private link the customer can open any time." focus="tracking">
            <Sw k="oe_allow_messages" label="Customers can message you" hint="Each status can allow or block it too (Statuses)." onFocus="tracking" />
            {cfg.oe_allow_messages !== false && (
              <div className="flex items-center gap-3 pb-3 pl-4">
                <span className="text-xs text-ink-secondary">Keep messages open after the job is finished for</span>
                <input type="number" min={0} max={365} value={cfg.oe_msg_days_after_close ?? 14} aria-label="Days after closing"
                  onChange={(e) => setCfg({ ...cfg, oe_msg_days_after_close: e.target.value === "" ? "" : Math.max(0, Math.min(365, Number(e.target.value))) })}
                  onBlur={() => save({ oe_msg_days_after_close: Number(cfg.oe_msg_days_after_close) || 0 })}
                  className="w-16 bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5 text-right" />
                <span className="text-xs text-ink-secondary">days</span>
              </div>
            )}
            <Sw k="oe_allow_staff_reply" label="Your team can reply on the page" onFocus="tracking" />
            <Sw k="oe_allow_recommend" label="Customers can approve extra work you recommend" onFocus="tracking" />
            <Sw k="oe_show_scheduled" label="Show the scheduled date" onFocus="tracking" />
          </Card>

          <Approval prefix="oe" list={est} noun="estimate" />
          <Approval prefix="oe_wo" list={wo} noun="work order" />

          {/* not <Card>: that helper is re-created each render, which would drop focus while typing */}
          <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
            <div className="flex items-start gap-3 px-5 pt-4 pb-3">
              <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="draw" c="text-lg" /></span>
              <div className="min-w-0"><h3 className="text-sm font-semibold text-ink-primary">Drop-off terms</h3><p className="text-[11px] text-ink-tertiary">What the customer reads and signs on the tablet when they leave an item (Tekly Go). Leave empty to use the standard wording.</p></div>
            </div>
            <div className="px-5">
            <textarea value={cfg.dropoff_terms ?? ""} maxLength={4000} rows={4} aria-label="Drop-off terms"
              onChange={(e) => setCfg({ ...cfg, dropoff_terms: e.target.value })}
              onBlur={() => save({ dropoff_terms: (cfg.dropoff_terms || "").trim() })}
              placeholder="I'm leaving this item for diagnosis and repair. I've backed up my data; the shop isn't responsible for data loss…"
              className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 mb-4 focus:outline-none focus:ring-1 focus:ring-primary" />
            </div>
          </section>
        </div>

        {/* Live preview */}
        <aside className="xl:sticky xl:top-4" aria-label="Preview">
          <div className="inline-flex rounded-xl bg-slate-100 p-1 mb-3">
            {([["booking", "Booking"], ["tracking", "Tracking"], ["estimate", "Estimate"]] as [typeof page, string][]).map(([v, l]) => (
              <button key={v} onClick={() => setPage(v)} aria-pressed={page === v}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${page === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
            ))}
          </div>
          <div className="rounded-[34px] border-[7px] border-slate-800 bg-white overflow-hidden mx-auto w-[260px] shadow-clay-card">
            <div className="h-[470px] overflow-hidden flex flex-col text-[11px]">
              <div className="flex items-center gap-2 px-3 py-3 border-b border-slate-100">
                <Logo show={!!cfg.page_show_logo} /><span className="font-semibold text-ink-primary truncate">{name}</span>
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
                      <div className="flex flex-wrap gap-1.5">{["Any", ...(cfg.public_employee_name === "first" ? ["Priya", "Amit"] : cfg.public_employee_name === "first_initial" ? ["Priya S.", "Amit R."] : cfg.public_employee_name === "custom" ? ["Priya S.", "Amit (lead tech)"] : ["Priya Shah", "Amit Rao"])].map((p, i) => <span key={p} className="px-2.5 py-1 rounded-full border text-[11px]" style={i === 0 ? { borderColor: accent, color: accent } : { borderColor: "#e2e8f0" }}>{p}</span>)}</div>
                    )}
                    <div className="grid grid-cols-4 gap-1.5">
                      {["9:00", "9:30", "10:00", "10:30", "11:00", "1:00", "1:30", "2:00"].map((t, i) => (
                        <span key={t} className="text-center rounded-lg py-1.5 border text-[11px]" style={i === 2 ? { background: accent, color: "#fff", borderColor: accent } : { borderColor: "#e2e8f0" }}>{t}</span>
                      ))}
                    </div>
                    <div className="rounded-xl py-2.5 text-center text-white font-semibold" style={{ background: accent }}>Book 10:00</div>
                  </div>
                )
              )}

              {page === "tracking" && (
                <div className="p-3 space-y-3 flex-1">
                  <div><div className="text-[11px] text-ink-tertiary">iPhone 14 · WO-1042</div><div className="text-sm font-semibold text-ink-primary">In repair</div></div>
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
                    <div className="flex justify-between"><span>Labour</span><span>$40.00</span></div>
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
          <p className="text-[11px] text-ink-tertiary text-center mt-2">Sample job and customer.</p>
        </aside>
      </div>
    </div>
  );
}
