import { useEffect, useRef, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import CurrencyManager from "../../components/CurrencyManager";
import { getLang, setLang, LANGS } from "../../i18n";
import { api } from "../../api/client";

/* Settings › General — the shop's profile. Edits land in local state and save together from a
   bar that only appears when something changed; a live preview shows how customers see the
   shop on paperwork and in texts. Same /staff/company payload as before (the hub saves it). */

type Opts = {
  countries: [string, string, string, string, string][];   // iso, name, timezone, currency code, symbol
  timezones: string[];
  currencies: [string, string][];
  docLangs: [string, string][];
  industries: [string, string][];
};

const SAVED_KEYS = ["name", "company_email", "company_phone", "company_address", "company_details", "company_note", "logo_data",
  "website", "country", "timezone", "currency", "currency_code", "document_language", "industry_template",
  "prohibit_dup_phone", "prohibit_dup_email", "require_mfa", "clock_wo_enabled", "clock_attendance_enabled",
  "clock_labour_billing", "rounding_enabled"];
const snap = (co: any) => JSON.stringify(SAVED_KEYS.map((k) => co?.[k] ?? null));

const INDUSTRY_ICON: Record<string, string> = {
  general: "inventory_2", auto: "directions_car", powersports: "bolt", marine: "sailing", mobile_phone: "smartphone",
  computer: "laptop", small_engine: "settings_suggest", tools: "hammer", bike: "pedal_bike", fleet_truck: "local_shipping",
};
const LEGACY: Record<string, string> = { generic: "general", device_repair: "mobile_phone", auto_repair: "auto", tool_repair: "tools", appliance_repair: "general" };

function Section({ icon, title, hint, children }: { icon: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
      <div className="flex items-start gap-3 px-5 pt-4 pb-3">
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n={icon} c="text-lg" /></span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink-primary">{title}</h3>
          {hint && <p className="text-[11px] text-ink-tertiary">{hint}</p>}
        </div>
      </div>
      <div className="px-5 pb-5">{children}</div>
    </section>
  );
}

const inp = "w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 placeholder:text-ink-tertiary";
function Field({ label, hint, children, wide }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={`block ${wide ? "sm:col-span-2" : ""}`}>
      <span className="block text-xs font-medium text-ink-secondary mb-1.5">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-ink-tertiary mt-1">{hint}</span>}
    </label>
  );
}

export default function GeneralSettings({ co, setCo, saved, onSave, rebuilding, onRebuild, opts }: {
  co: any; setCo: (fn: (s: any) => any) => void; saved: { current: any }; onSave: () => Promise<boolean>; rebuilding: boolean; onRebuild: () => void; opts: Opts;
}) {
  // `saved` lives in the hub so leaving the section and coming back still shows unsaved edits.
  if (saved.current == null) saved.current = co;
  const [saving, setSaving] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  // The industry list comes from the server (same registry Industry & assets uses); the static
  // list is only a fallback if that call fails.
  const [tpls, setTpls] = useState<{ key: string; label: string; asset_label?: string; asset_label_plural?: string }[] | null>(null);
  useEffect(() => { api.get<any>("/settings/industry-template").then((r) => setTpls(r.templates || null)).catch(() => setTpls(null)); }, []);
  const industryTiles = tpls?.length
    ? tpls.map((t) => ({ key: t.key, name: t.label, noun: t.asset_label_plural || t.asset_label || "" }))
    : opts.industries.map(([v, l]) => { const [name, noun] = l.split(" — "); return { key: v, name: name.replace(/^Generic$/, "Something else"), noun: noun || "" }; });
  const currentIndustry = LEGACY[co.industry_template] || co.industry_template || "general";
  const dirty = snap(co) !== snap(saved.current);
  const set = (k: string, v: any) => setCo((s: any) => ({ ...s, [k]: v }));

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function readLogo(f?: File | null) {
    if (!f || !f.type.startsWith("image/")) return;
    const r = new FileReader(); r.onload = () => set("logo_data", r.result as string); r.readAsDataURL(f);
  }
  const pickCountry = (iso: string) => {
    const m = opts.countries.find((c) => c[0] === iso);
    setCo((s: any) => ({ ...s, country: iso, ...(m ? { timezone: m[2], currency_code: m[3], currency: m[4] } : {}) }));
  };
  const pickCurrency = (code: string) => {
    const c = opts.currencies.find((x) => x[0] === code);
    setCo((s: any) => ({ ...s, currency_code: code, ...(c ? { currency: c[1] } : {}) }));
  };
  async function save() {
    setSaving(true);
    const ok = await onSave();
    setSaving(false);
    if (ok) saved.current = co;
  }
  const discard = () => setCo(() => saved.current);

  const Switch = ({ k, label, hint }: { k: string; label: string; hint?: string }) => (
    <label className="flex items-center gap-3 py-3 border-t border-slate-100 first:border-0 cursor-pointer">
      <span className="min-w-0"><span className="block text-sm text-ink-primary">{label}</span>{hint && <span className="block text-[11px] text-ink-tertiary">{hint}</span>}</span>
      <button type="button" role="switch" aria-checked={!!co?.[k]} aria-label={label} onClick={() => set(k, !co?.[k])}
        className={`ml-auto w-10 h-6 rounded-full flex-shrink-0 relative transition-colors ${co?.[k] ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${co?.[k] ? "left-[18px]" : "left-0.5"}`} /></button>
    </label>
  );

  const country = opts.countries.find((c) => c[0] === co.country);
  const contact = [co.company_phone, co.company_email, co.website].filter(Boolean);
  const first = (co.name || "Your shop").trim();

  return (
    <div className="max-w-5xl pb-24">
      <div className="mb-5">
        <h2 className="text-base font-semibold text-ink-primary">Your shop</h2>
        <p className="text-xs text-ink-tertiary">How your business shows up on invoices, receipts, texts and the customer tracking page.</p>
      </div>

      <div className="grid xl:grid-cols-[minmax(0,1fr)_300px] gap-5 items-start">
        <div className="space-y-5 min-w-0">
          <Section icon="storefront" title="Shop details" hint="Shown on every document and message.">
            <div className="flex items-center gap-4 mb-5">
              <button type="button" onClick={() => fileRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
                onDrop={(e) => { e.preventDefault(); setDragOver(false); readLogo(e.dataTransfer.files?.[0]); }}
                aria-label={co.logo_data ? "Change logo" : "Add logo"}
                className={`w-20 h-20 rounded-2xl border-2 border-dashed grid place-items-center overflow-hidden flex-shrink-0 transition-colors ${dragOver ? "border-primary bg-primary/5" : co.logo_data ? "border-transparent bg-slate-50" : "border-slate-300 bg-slate-50 hover:border-primary/60"}`}>
                {co.logo_data ? <img src={co.logo_data} alt="Shop logo" className="w-full h-full object-contain" /> : <M n="upload" c="text-xl text-ink-tertiary" />}
              </button>
              <input ref={fileRef} type="file" accept="image/*" onChange={(e) => readLogo(e.target.files?.[0])} className="hidden" />
              <div className="min-w-0">
                <div className="text-sm text-ink-primary">{co.logo_data ? "Your logo" : "Add your logo"}</div>
                <div className="text-[11px] text-ink-tertiary">Drop an image here or click to choose. A square PNG looks best.</div>
                {co.logo_data && <button onClick={() => set("logo_data", "")} className="mt-1 text-[11px] font-semibold text-rose-600 hover:underline">Remove logo</button>}
              </div>
            </div>
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Shop name" wide><input value={co.name || ""} onChange={(e) => set("name", e.target.value)} placeholder="Fix-it Pro Repairs" className={`${inp} text-base font-medium`} /></Field>
              <Field label="Phone"><input value={co.company_phone || ""} onChange={(e) => set("company_phone", e.target.value)} placeholder="(555) 010-2030" className={inp} /></Field>
              <Field label="Email"><input type="email" value={co.company_email || ""} onChange={(e) => set("company_email", e.target.value)} placeholder="hello@yourshop.com" className={inp} /></Field>
              <Field label="Website"><input value={co.website || ""} onChange={(e) => set("website", e.target.value)} placeholder="yourshop.com" className={inp} /></Field>
              <Field label="Address"><input value={co.company_address || ""} onChange={(e) => set("company_address", e.target.value)} placeholder="123 Main St, Austin, TX" className={inp} /></Field>
            </div>
          </Section>

          <Section icon="language" title="Region, money and language" hint="Picking a country fills in the time zone and currency. You can still change them.">
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Country">
                <select value={co.country || ""} onChange={(e) => pickCountry(e.target.value)} className={inp}>
                  <option value="">Choose a country</option>{opts.countries.map((c) => <option key={c[0]} value={c[0]}>{c[1]}</option>)}
                </select>
              </Field>
              <Field label="Time zone">
                <select value={co.timezone || ""} onChange={(e) => set("timezone", e.target.value)} className={inp}>
                  <option value="">Choose a time zone</option>{opts.timezones.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
                </select>
              </Field>
              <Field label="Currency">
                <select value={co.currency_code || ""} onChange={(e) => pickCurrency(e.target.value)} className={inp}>
                  <option value="">Choose a currency</option>{opts.currencies.map(([c, s]) => <option key={c} value={c}>{c} ({s})</option>)}
                </select>
              </Field>
              <Field label="Currency symbol"><input value={co.currency || ""} onChange={(e) => set("currency", e.target.value)} className={inp} /></Field>
              <Field label="Language on printed documents">
                <select value={co.document_language || "en"} onChange={(e) => set("document_language", e.target.value)} className={inp}>
                  {opts.docLangs.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
              <Field label="My language in the app" hint="Just for you, on this device. Saved right away.">
                <select value={getLang()} onChange={(e) => setLang(e.target.value as any)} className={inp}>
                  {LANGS.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
                </select>
              </Field>
            </div>
          </Section>

          <Section icon="description" title="On your paperwork" hint="Printed on invoices, receipts and estimates.">
            <div className="grid gap-4">
              <Field label="Registration or tax ID"><input value={co.company_details || ""} onChange={(e) => set("company_details", e.target.value)} placeholder="EIN 12-3456789" className={inp} /></Field>
              <Field label="Note at the bottom of documents"><textarea value={co.company_note || ""} onChange={(e) => set("company_note", e.target.value)} rows={2} placeholder="90-day warranty on all repairs. Thank you for your business!" className={`${inp} resize-y`} /></Field>
            </div>
          </Section>

          <CurrencyManager />

          <Section icon="handyman" title="What you repair" hint="Sets the words used across the app (vehicles, vessels, devices…), the ID to ask for, and starter brands and models. Jobs you already have keep their wording.">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
              {industryTiles.map(({ key: v, name, noun }) => {
                const on = currentIndustry === v;
                return (
                  <button key={v} type="button" onClick={() => set("industry_template", v)} aria-pressed={on}
                    className={`relative text-left rounded-xl border p-3 transition-colors ${on ? "border-primary bg-primary/5 ring-2 ring-primary/15" : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"}`}>
                    {on && <span className="absolute top-2 right-2 w-5 h-5 rounded-full bg-primary text-white grid place-items-center"><M n="check" c="text-[12px]" /></span>}
                    <span className={`w-8 h-8 rounded-lg grid place-items-center mb-2 ${on ? "bg-primary text-white" : "bg-slate-100 text-ink-secondary"}`}><M n={INDUSTRY_ICON[v] || "inventory_2"} c="text-base" /></span>
                    <span className="block text-xs font-semibold text-ink-primary pr-5">{name}</span>
                    {noun && <span className="block text-[11px] text-ink-tertiary">{noun}</span>}
                  </button>
                );
              })}
            </div>
          </Section>

          <div className="grid md:grid-cols-2 gap-5">
            <Section icon="timer" title="Time tracking">
              <Switch k="clock_wo_enabled" label="Job and task timer" hint="Track time spent on jobs" />
              <Switch k="clock_attendance_enabled" label="Clock in and out" hint="Shop attendance" />
              <Switch k="clock_labour_billing" label="Bill labour from the timer" hint="Time × the tech's rate adds a labour line" />
            </Section>
            <Section icon="verified_user" title="Rules and security">
              <Switch k="prohibit_dup_phone" label="No duplicate phone numbers" hint="Stops two customers with the same phone" />
              <Switch k="prohibit_dup_email" label="No duplicate emails" />
              <Switch k="rounding_enabled" label="Round invoice totals" />
              <Switch k="require_mfa" label="Two-step sign-in for everyone" hint="Staff can't turn it off for themselves" />
            </Section>
          </div>

          <div className="flex items-center gap-3 rounded-2xl border border-slate-200/90 bg-white px-5 py-4">
            <span className="w-9 h-9 rounded-xl bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n="refresh" c="text-lg" /></span>
            <div className="min-w-0">
              <div className="text-sm text-ink-primary">Recalculate balances</div>
              <div className="text-[11px] text-ink-tertiary">Rebuilds every customer and supplier balance from history. Safe to run any time.</div>
            </div>
            <button onClick={onRebuild} disabled={rebuilding} className="ml-auto px-3.5 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50 disabled:opacity-60 flex-shrink-0">{rebuilding ? "Working…" : "Recalculate"}</button>
          </div>
        </div>

        {/* Live preview */}
        <aside className="xl:sticky xl:top-4 space-y-3" aria-label="Preview">
          <div className="text-[11px] font-semibold text-ink-tertiary px-1">How customers see you</div>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="h-1.5 bg-primary" />
            <div className="p-4">
              <div className="flex items-start gap-3">
                <span className="w-11 h-11 rounded-xl bg-slate-50 border border-slate-100 grid place-items-center overflow-hidden flex-shrink-0">
                  {co.logo_data ? <img src={co.logo_data} alt="" className="w-full h-full object-contain" /> : <span className="text-sm font-semibold text-ink-secondary">{first.slice(0, 1).toUpperCase()}</span>}
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-ink-primary truncate">{first}</div>
                  {co.company_address && <div className="text-[11px] text-ink-tertiary leading-snug">{co.company_address}</div>}
                </div>
                <span className="ml-auto text-[11px] font-semibold text-ink-tertiary ">INVOICE</span>
              </div>
              {contact.length > 0 && <div className="mt-2 text-[11px] text-ink-secondary leading-relaxed break-words">{contact.join(" · ")}</div>}
              {co.company_details && <div className="text-[11px] text-ink-tertiary">{co.company_details}</div>}
              <div className="mt-3 border-t border-dashed border-slate-200 pt-3 space-y-1.5 text-[11px]">
                <div className="flex justify-between text-ink-secondary"><span>Screen replacement</span><span>{co.currency || "$"}189.00</span></div>
                <div className="flex justify-between text-ink-secondary"><span>Labour</span><span>{co.currency || "$"}40.00</span></div>
                <div className="flex justify-between font-semibold text-ink-primary pt-1"><span>Total</span><span>{co.currency || "$"}229.00</span></div>
              </div>
              {co.company_note && <div className="mt-3 text-[11px] text-ink-tertiary italic leading-snug">{co.company_note}</div>}
            </div>
          </div>
          <div className="flex items-end gap-2">
            <span className="w-7 h-7 rounded-full bg-slate-100 grid place-items-center flex-shrink-0 text-ink-secondary"><M n="smartphone" c="text-[13px]" /></span>
            <div className="bg-white border border-slate-200/90 rounded-2xl rounded-bl-md px-3 py-2 text-[12px] text-ink-primary leading-snug shadow-clay-card">
              {first}: Hi Sam, your repair is ready for pickup. Total {co.currency || "$"}229.00.
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5 px-1">
            {country && <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 text-[11px] text-ink-secondary"><M n="public" c="text-[12px]" />{country[1]}</span>}
            {co.timezone && <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 text-[11px] text-ink-secondary"><M n="schedule" c="text-[12px]" />{co.timezone.replace(/_/g, " ")}</span>}
            {co.currency_code && <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 text-[11px] text-ink-secondary"><M n="payments" c="text-[12px]" />{co.currency_code}</span>}
          </div>
        </aside>
      </div>

      {dirty && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 bg-ink-primary text-white rounded-2xl pl-4 pr-2 py-2 shadow-clay-drawer">
          <span className="w-2 h-2 rounded-full bg-amber-400" />
          <span className="text-xs">You have unsaved changes</span>
          <button onClick={discard} className="px-3 py-1.5 rounded-lg text-xs text-white/80 hover:text-white hover:bg-white/10">Discard</button>
          <button onClick={save} disabled={saving} className="px-4 py-1.5 rounded-lg bg-white text-ink-primary text-xs font-semibold hover:bg-white/90 disabled:opacity-60">{saving ? "Saving…" : "Save changes"}</button>
        </div>
      )}
    </div>
  );
}
