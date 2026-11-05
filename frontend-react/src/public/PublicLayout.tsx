import { useLayoutEffect, type CSSProperties, type ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import { TeklyLogo, TeklyMark } from "../components/brand/TeklyLogo";
import { IconButton } from "../components/Button";

// The one frame for every page a customer (or a signed-out person) sees: tracking, the portal,
// check-in, booking, the repair lookup and the sign-in pages. One width, one header (the shop's
// logo or initials tile, or the Tekly mark when there is no shop), one footer credit, one title
// size, one field look. Customer pages are always light (owner decision D4): they never follow
// the visitor's dark mode, so the shop's colour always reads the same.

export const SAPPHIRE = "#0059ba";
// The colour a shop has before it picks one in Settings › Public pages (the database default).
const UNSET_COLOR = "#3b6fe0";

/** Keep the page light, whatever the device or a previous page set. */
export function useLightOnly() {
  useLayoutEffect(() => {
    const el = document.documentElement;
    el.classList.remove("dark");
    el.style.colorScheme = "light";
  }, []);
}

/** The shop's chosen accent for buttons and highlights; sapphire when it never picked one. */
export function shopAccent(c?: string | null) {
  const v = (c || "").trim();
  if (!/^#[0-9a-f]{6}$/i.test(v) || v.toLowerCase() === UNSET_COLOR) return SAPPHIRE;
  return v;
}

/** White or near-black text on a colour, whichever reads better. */
export function onColor(hex: string) {
  const h = hex.replace("#", ""); const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => { const x = c / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? "#111827" : "#ffffff";
}

/** Inline colours for a main button in the shop's accent (nothing when it is plain sapphire). */
export function accentFill(accent: string): CSSProperties | undefined {
  return accent === SAPPHIRE ? undefined : { background: accent, color: onColor(accent) };
}

export const initials = (s?: string | null) =>
  (s || "").trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "•";

export type ShopInfo = { name?: string | null; logo?: string | null };

/** The shop's logo, or its initials on a sapphire tile: the same on every customer page. */
export function ShopTile({ shop }: { shop: ShopInfo }) {
  if (shop.logo) return <img src={shop.logo} alt="" className="h-9 w-9 shrink-0 rounded-xl bg-white object-contain" />;
  return (
    <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-sm font-semibold text-white" style={{ background: SAPPHIRE }}>
      {initials(shop.name)}
    </span>
  );
}

// One look for the cards and the fields on these pages (44px fields, labels always above).
export const publicCard = "rounded-2xl border border-slate-200 bg-white shadow-clay-card";
export const inputCls = "h-11 w-full rounded-xl border border-slate-300 bg-white px-3.5 text-base text-ink-primary outline-none transition placeholder:text-ink-placeholder focus:border-primary focus:ring-[3px] focus:ring-primary/20 disabled:opacity-60";
export const selectCls = `${inputCls} appearance-none pr-10`;
export const textareaCls = "w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-base text-ink-primary outline-none transition placeholder:text-ink-placeholder focus:border-primary focus:ring-[3px] focus:ring-primary/20";
export const fieldLabelCls = "mb-1.5 block text-sm font-medium text-ink-secondary";

/** A labelled field: the label above, always visible. `right` sits inside the box (an eye or a chevron). */
export function Field({ id, label, children, right, hint }: {
  id: string; label: ReactNode; children: ReactNode; right?: ReactNode; hint?: ReactNode;
  /** Kept so older callers compile: every field now has its label above. */
  select?: boolean;
}) {
  return (
    <div>
      <label htmlFor={id} className={fieldLabelCls}>{label}</label>
      <div className="relative">{children}{right}</div>
      {hint && <p className="mt-1.5 text-sm text-ink-tertiary">{hint}</p>}
    </div>
  );
}

/** The page's title and one line under it. Every customer and sign-in page uses this one size. */
export function PublicTitle({ title, sub, badge, center = false }: { title: ReactNode; sub?: ReactNode; badge?: ReactNode; center?: boolean }) {
  return (
    <div className={center ? "text-center" : ""}>
      {badge}
      <h1 className="text-2xl font-semibold leading-tight tracking-tight text-ink-primary sm:text-display">{title}</h1>
      {sub && <p className="mt-1.5 text-base text-ink-tertiary">{sub}</p>}
    </div>
  );
}

/** "Powered by Tekly", with the mark. The one credit on every customer and sign-in page. */
export function PublicFooter({ place }: { place?: string | null }) {
  return (
    <footer className="flex items-center justify-center gap-1.5 px-4 py-8 text-xs text-ink-tertiary">
      {place && <span className="truncate">{place} ·</span>}
      <TeklyMark size={14} />
      <span className="whitespace-nowrap">Powered by Tekly</span>
    </footer>
  );
}

export default function PublicLayout({ shop, title, right, onBack, backLabel = "Back", place, children, bottomSpace = false }: {
  /** The shop: its tile and name head the page. `null` while it loads; leave it out on a Tekly page. */
  shop?: ShopInfo | null;
  /** The line beside the Tekly logo when there is no shop (e.g. "Track your repair"). */
  title?: ReactNode;
  right?: ReactNode;
  onBack?: () => void;
  backLabel?: string;
  /** Shown before the footer credit, e.g. the location's name. */
  place?: string | null;
  children: ReactNode;
  /** Leave room at the bottom for a fixed action bar. */
  bottomSpace?: boolean;
}) {
  useLightOnly();
  return (
    <div className={`flex min-h-screen flex-col bg-canvas font-sans text-ink-primary ${bottomSpace ? "pb-24" : ""}`}>
      <div className="mx-auto w-full max-w-[640px] flex-1 px-4 sm:px-6">
        <header className="flex items-center gap-3 pt-6 sm:pt-10">
          {onBack && <IconButton label={backLabel} variant="secondary" onClick={onBack} icon={<ChevronLeft size={18} />} />}
          {shop === null ? (
            <span className="sk h-9 w-40 rounded-xl" aria-hidden="true" />
          ) : shop ? (<>
            <ShopTile shop={shop} />
            <span className="min-w-0 flex-1 truncate text-base font-semibold text-ink-primary">{shop.name}</span>
          </>) : (<>
            <a href="/login" aria-label="Tekly" className="flex h-9 shrink-0 items-center"><TeklyLogo size={28} /></a>
            {title && <span className="min-w-0 flex-1 truncate border-l border-slate-200 pl-3 text-sm font-medium text-ink-secondary">{title}</span>}
          </>)}
          {right && <span className="ml-auto shrink-0">{right}</span>}
        </header>
        <main className="pt-8">{children}</main>
      </div>
      <PublicFooter place={place} />
    </div>
  );
}
