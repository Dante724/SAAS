import { ReactNode, useEffect, useRef, useState } from "react";
import DeviceList from "./DeviceList";
import { useVisibleInterval } from "../hooks/useVisibleInterval";
import { toAppPath } from "../lib/deepLink";
import { TeklyMark } from "./brand/TeklyLogo";
import { NavLink, useNavigate } from "react-router-dom";
import { api, getLocationScope, setLocationScope } from "../api/client";
import { alertDialog } from "../lib/dialog";
import { t, getLang, setLang, applyDir } from "../i18n";
import { useMe, NAV_PERM } from "../context/Me";
import {
  Wrench, Mail, MessageSquare, MessageCircle, Calendar, CalendarRange, Users, User,
  UserPlus, ShoppingCart, Package, Boxes, Landmark, Wallet, BarChart3, Settings as SettingsIcon,
  MoreVertical, MoreHorizontal, Search, Rows3, Columns3, KanbanSquare, Pause, Play, Bell,
  ClipboardList, ClipboardCheck, Banknote, IndianRupee, AlertTriangle, AlertCircle, Plus, Minus,
  Filter, SlidersHorizontal, Bookmark, BookmarkCheck, ChevronRight, ChevronDown, Camera,
  Image as ImageIcon, Undo2, ArrowLeft, ArrowRight, ArrowLeftRight, Sparkles, Barcode, QrCode,
  ScanLine, Zap, Check, CheckCircle2, X, LifeBuoy, CreditCard, Trash2, FileText, HardHat, Flag,
  Link as LinkIcon, ExternalLink, Printer, Receipt, ReceiptText, Save, PiggyBank, Clock, Send,
  Smartphone, RefreshCw, Timer, TrendingDown, TrendingUp, Warehouse, Contact, Circle,
  LayoutDashboard, Hammer, Sun, Moon, Languages,
  Pencil, Eye, Braces, Bold, Italic, Underline, Heading1, Heading2, List,
  AlignLeft, AlignCenter, AlignRight, Table, Baseline, Highlighter, Store, Gavel,
  MonitorSmartphone, BadgeCheck, Tag, GripVertical, Heading, Sigma, StickyNote,
  Signature, Square, ChevronUp, Strikethrough, Code, Copy,
  Rows2, Rows4, SearchX, ClipboardPaste, FilterX, ChevronLeft,
  SquarePlus, CirclePlus, Archive, ArrowDown, ArrowUp, ClipboardX, Paperclip, ScanBarcode,
  Ban, CircleX, ListChecks, Pipette, ScanText, CheckCheck, Download, FilePen, CalendarCheck,
  History, Hourglass, LineChart, Lightbulb, Unlink, Truck, Lock, LockOpen, Merge,
  CircleDollarSign, Percent, ShieldAlert, Globe, Gift, RotateCcw, SearchCheck, VideoOff,
  ShieldCheck, Target, EyeOff, Car, Sailboat, Bike, Laptop, Cog, Upload,
  ShoppingBag, Split, Nfc, Phone, PhoneIncoming, PhoneOutgoing, PhoneMissed, Network, HelpCircle,
  LogOut, Menu as MenuIcon, PackageOpen, Info,
  CircleDot, CloudOff, Footprints, Hand, Infinity as InfinityIcon, LayoutGrid, LogIn, MessageSquareText, MessagesSquare, MoveRight, Redo2, SquareCheck, Star, ThumbsUp, TriangleAlert, UserX,
} from "lucide-react";
import { fmtServerTime } from "../lib/serverTime";

// Map the old Material-Symbols ligature names to bundled Lucide icons (no web font,
// works under the app's Content-Security-Policy, and needs no server restart).
const ICONS: Record<string, any> = {
  // Names used in the app that had no mapping (they drew as an empty circle).
  forum: MessagesSquare, grid_view: LayoutGrid, summarize: FileText, reviews: Star,
  check_box: SquareCheck, check_box_outline_blank: Square, sms: MessageSquareText, thumb_up: ThumbsUp,
  directions_walk: Footprints, insert_chart: BarChart3, login: LogIn, all_inclusive: InfinityIcon, bedtime: Moon,
  list_alt: ClipboardList, back_hand: Hand, cloud_off: CloudOff, sync_problem: TriangleAlert,
  trending_flat: MoveRight, person_off: UserX, redo: Redo2, replay: RotateCcw, print_disabled: Printer,
  currency_exchange: ArrowLeftRight, fiber_manual_record: CircleDot, radio_button_unchecked: Circle,
  package_2: PackageOpen, info: Info,
  build: Wrench, handyman: Wrench, engineering: HardHat, hammer: Hammer,
  task_alt: CheckCircle2, mail: Mail, chat: MessageSquare, chat_bubble: MessageCircle,
  calendar_month: Calendar, calendar_view_week: CalendarRange, schedule: Clock, timer: Timer,
  contacts: Contact, group: Users, person: User, person_add: UserPlus, contact_support: LifeBuoy,
  point_of_sale: ShoppingCart, shopping_cart: ShoppingCart, shopping_bag: ShoppingBag, call_split: Split,
  inventory_2: Package, inventory: Boxes, warehouse: Warehouse,
  account_balance: Landmark, payments: Wallet, account_balance_wallet: Wallet, savings: PiggyBank,
  credit_card: CreditCard, request_quote: ReceiptText, receipt_long: Receipt, description: FileText,
  analytics: BarChart3, bar_chart: BarChart3, trending_up: TrendingUp, trending_down: TrendingDown,
  settings: SettingsIcon, tune: SlidersHorizontal,
  pending_actions: ClipboardList, fact_check: ClipboardCheck,
  attach_money: Banknote, currency_rupee: IndianRupee,
  priority_high: AlertCircle, warning: AlertTriangle, flag: Flag, bolt: Zap, auto_awesome: Sparkles,
  add: Plus, remove: Minus, close: X, menu: MenuIcon, check: Check, check_circle: CheckCircle2,
  delete: Trash2, delete_sweep: Trash2, undo: Undo2, assignment_return: Undo2,
  swap_horiz: ArrowLeftRight, sync_alt: RefreshCw,
  filter_list: Filter, filter_alt: Filter, view_column: Columns3,
  bookmark: Bookmark, bookmark_added: BookmarkCheck,
  chevron_right: ChevronRight, chevron_left: ChevronLeft, expand_more: ChevronDown, arrow_back: ArrowLeft, arrow_forward: ArrowRight,
  // 144y — icons that were falling back to a blank Circle (app-wide sweep)
  add_box: SquarePlus, add_circle: CirclePlus, add_shopping_cart: ShoppingCart, archive: Archive,
  arrow_downward: ArrowDown, arrow_upward: ArrowUp, assignment_late: ClipboardX, attach_file: Paperclip,
  autorenew: RefreshCw, barcode_scanner: ScanBarcode, block: Ban, cancel: CircleX, checklist: ListChecks,
  colorize: Pipette, delete_forever: Trash2, document_scanner: ScanText, done_all: CheckCheck,
  download: Download, edit_note: FilePen, error: AlertCircle, event_available: CalendarCheck,
  group_add: UserPlus, groups: Users, history: History, hourglass_empty: Hourglass, insights: LineChart,
  lightbulb: Lightbulb, link_off: Unlink, local_shipping: Truck, lock: Lock, lock_clock: Lock,
  lock_open: LockOpen, merge: Merge, paid: CircleDollarSign, percent: Percent, privacy_tip: ShieldAlert,
  public: Globe, redeem: Gift, refresh: RefreshCw, report: Flag, restore: RotateCcw, rule: ListChecks,
  search_check: SearchCheck, sell: Tag, stop: Square, table_view: Table, tag: Tag, target: Target,
  verified: BadgeCheck, verified_user: ShieldCheck, videocam_off: VideoOff, web: Globe,
  qr_code_2: QrCode, sticky_note_2: StickyNote, receipt: Receipt,
  open_in_new: ExternalLink, link: LinkIcon,
  search: Search, table_rows: Rows3, view_kanban: KanbanSquare,
  notifications: Bell, pause: Pause, play_arrow: Play, more_vert: MoreVertical, more_horiz: MoreHorizontal,
  print: Printer, save: Save, send: Send, campaign: Send, smartphone: Smartphone,
  photo_camera: Camera, add_a_photo: Camera, image: ImageIcon,
  barcode: Barcode, qr_code_scanner: QrCode, scan: ScanLine, contactless: Nfc,
  call: Phone, phone: Phone, call_received: PhoneIncoming, phone_missed: PhoneMissed, hub: Network, help: HelpCircle, call_made: PhoneOutgoing,
  dashboard: LayoutDashboard, light_mode: Sun, dark_mode: Moon, language: Languages, logout: LogOut,
  // 144hx — document editor icons (were falling back to Circle)
  edit: Pencil, visibility: Eye, directions_car: Car, sailing: Sailboat, pedal_bike: Bike, laptop: Laptop, settings_suggest: Cog, upload: Upload, visibility_off: EyeOff, data_object: Braces, expand_less: ChevronUp,
  format_bold: Bold, format_italic: Italic, format_underlined: Underline,
  title: Heading1, text_fields: Heading2, format_list_bulleted: List,
  format_align_left: AlignLeft, format_align_center: AlignCenter, format_align_right: AlignRight,
  horizontal_rule: Minus, table: Table, format_color_text: Baseline, format_ink_highlighter: Highlighter,
  storefront: Store, gavel: Gavel, devices: MonitorSmartphone, badge: BadgeCheck,
  calendar_today: Calendar, label: Tag, drag_indicator: GripVertical, view_headline: Heading,
  functions: Sigma, notes: StickyNote, draw: Signature, crop_square: Square,
  strikethrough_s: Strikethrough, code: Code, content_copy: Copy,
  // 144bw — inventory craft icons (were falling back to Circle)
  density_medium: Rows2, density_small: Rows4, search_off: SearchX,
  content_paste: ClipboardPaste, filter_alt_off: FilterX, bookmark_add: Bookmark,
};

function sizeFromClass(c: string): number {
  if (/text-(lg|xl|2xl)\b|text-\[(1[89]|2\d)px\]/.test(c)) return 20;
  if (/text-\[17px\]|text-base\b/.test(c)) return 17;
  if (/text-sm\b/.test(c)) return 15;
  if (/text-(xs|\[1[0-4]px\])\b|text-\[1[0-3]px\]/.test(c)) return 14;
  return 16;
}

// Icon component — same call signature as before (<M n="..." c="..." />), so every
// page keeps working unchanged.
export const M = ({ n, c = "" }: { n: string; c?: string }) => {
  const Cmp = ICONS[n] || Circle;
  return <Cmp className={c} size={sizeFromClass(c)} strokeWidth={2} aria-hidden />;
};

// Left navigation — real routes, grouped into sections so the rail reads as a
// workspace, not a flat wall of links. Section name "" renders with no header
// (the home anchor). NAV (flat) is derived for the command palette + perms.
type NavItem = { to: string; i: string; l: string; end?: boolean };
const NAV_GROUPS: { name: string; items: NavItem[] }[] = [
  { name: "", items: [{ to: "/", i: "dashboard", l: "Home", end: true }] },
  { name: "Front desk", items: [
    { to: "/inquiries", i: "mail", l: "Inquiries" },
    { to: "/appointments", i: "calendar_month", l: "Appointments" },
    { to: "/chats", i: "chat", l: "Chats" },
    { to: "/clients", i: "contacts", l: "Contacts" },
  ] },
  { name: "Workshop", items: [
    { to: "/orders", i: "handyman", l: "Work orders" },
    { to: "/my-work", i: "engineering", l: "My work" },
    { to: "/timesheets", i: "schedule", l: "Timesheets" },
    { to: "/tasks", i: "task_alt", l: "Tasks" },
  ] },
  { name: "Commerce", items: [
    { to: "/sales", i: "point_of_sale", l: "Sales" },
    { to: "/invoices", i: "request_quote", l: "Invoices" },
    { to: "/catalog", i: "sell", l: "Catalog" },
    { to: "/inventory", i: "inventory_2", l: "Inventory" },
    { to: "/finance", i: "account_balance", l: "Finance" },
  ] },
  { name: "Grow", items: [
    { to: "/marketing", i: "campaign", l: "Marketing" },
    { to: "/reports", i: "analytics", l: "Reports" },
  ] },
  { name: "Admin", items: [
    { to: "/settings", i: "settings", l: "Settings" },
  ] },
];
const NAV: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

export default function Shell({ title, children }: { title: string; children: ReactNode }) {
  const { can, me, refresh } = useMe();
  const initials = ((me?.name || "").trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("") || "?").toUpperCase();
  const [palette, setPalette] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [secOpen, setSecOpen] = useState(false);
  // Org requires 2FA and this user hasn't enrolled: the API refuses everything except enrollment,
  // so show a setup screen that can't be dismissed. Sticky until the page reloads, so the
  // recovery codes stay on screen after `me` refreshes to enabled.
  const [mfaGate, setMfaGate] = useState(false);
  useEffect(() => { if (me?.mfa_required && !me?.mfa_enabled) setMfaGate(true); }, [me?.mfa_required, me?.mfa_enabled]);
  const [avatarOpen, setAvatarOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [dark, setDark] = useState(() => { try { return localStorage.getItem("ros_theme") === "dark"; } catch { return false; } });
  const [railCollapsed, setRailCollapsed] = useState(() => { try { return localStorage.getItem("ros_nav_rail") === "1"; } catch { return false; } });
  const toggleRail = () => setRailCollapsed((c) => { const n = !c; try { localStorage.setItem("ros_nav_rail", n ? "1" : "0"); } catch { /* ignore */ } return n; });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("ros_theme", dark ? "dark" : "light"); } catch { /* ignore */ }
  }, [dark]);
  useEffect(() => { applyDir(); }, []);  // mirror layout for RTL languages (Arabic) on load
  const lang = getLang();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
      if (e.key === "Escape") { setPalette(false); setNotifOpen(false); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const liveRef = useRef(true);
  useEffect(() => { liveRef.current = true; return () => { liveRef.current = false; }; }, []);
  useVisibleInterval(() => {
    api.get<{ items: any[] }>("/tech/notifications?only_unread=true")
      .then((r) => { if (liveRef.current) setUnread((r.items || []).length); }).catch(() => {});
  }, 60000);

  // Below 900px the sidebar becomes a slide-in drawer opened from the top bar's menu button.
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setNavOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  // The nav list, shared by the desktop sidebar (optionally a narrow icon rail) and the phone drawer.
  const renderNav = (collapsed: boolean, onNavigate?: () => void) => (
    <>
      {NAV_GROUPS.map((group) => {
        const visible = group.items.filter((it) => can(NAV_PERM[it.to]));
        if (visible.length === 0) return null;
        return (
          <div key={group.name || "home"} className="pb-1">
            {group.name && !collapsed && (
              <div className="px-2.5 pt-2.5 pb-1 text-[11px] font-bold text-ink-tertiary/80">{t(group.name)}</div>
            )}
            {group.name && collapsed && <div className="my-1.5 mx-2 border-t border-slate-200/70" />}
            {visible.map((it) => (
              <NavLink
                key={it.to}
                to={it.to}
                end={it.end}
                onClick={onNavigate}
                title={collapsed ? t(it.l) : undefined}
                className={({ isActive }) =>
                  `flex items-center gap-2 px-2.5 rounded-lg text-xs transition-colors ${onNavigate ? "py-2.5 text-[13px]" : "py-1.5"} ${collapsed ? "justify-center" : ""} ${
                    isActive
                      ? "font-bold text-primary bg-blue-50/80 shadow-sm"
                      : "font-medium text-ink-secondary hover:bg-slate-100 hover:text-ink-primary"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <M n={it.i} c={`text-[17px] ${isActive ? "text-primary" : "text-ink-tertiary"}`} />
                    {!collapsed && <span>{t(it.l)}</span>}
                  </>
                )}
              </NavLink>
            ))}
          </div>
        );
      })}
    </>
  );
  const brand = (collapsed: boolean) => (
    <div className={`flex items-center gap-2.5 px-2 pt-1 pb-3 ${collapsed ? "justify-center" : ""}`}>
      <TeklyMark size={32} />
      {!collapsed && (
        <div>
          <div className="font-bold text-[17px] leading-none tracking-[-0.05em] text-ink-primary flex items-center gap-1.5" aria-label="Tekly">tekly <span className="text-[10px] tracking-normal px-1 py-px rounded bg-blue-100 text-primary font-bold">PRO</span></div>
          {me?.org_name && <p className="max-w-[140px] truncate text-[11px] text-ink-tertiary font-medium" title={me.org_name}>{me.org_name}</p>}
        </div>
      )}
    </div>
  );

  return (
    <div className="bg-surface text-ink-primary antialiased h-screen h-[100dvh] w-full overflow-hidden select-none flex flex-col font-sans">
      <div className="flex flex-1 overflow-hidden relative">
        {/* SIDEBAR (900px and up) */}
        <aside className={`${railCollapsed ? "w-[64px]" : "w-[200px]"} hidden min-[900px]:flex flex-shrink-0 bg-sidebar border-r border-slate-200/80 flex-col justify-between z-10 transition-[width] duration-200`}>
          <nav aria-label={t("Main menu")} className="p-3 space-y-0.5 overflow-y-auto">
            {brand(railCollapsed)}
            {renderNav(railCollapsed)}
            <button onClick={toggleRail} title={railCollapsed ? t("Expand menu") : t("Collapse menu")} aria-label={railCollapsed ? t("Expand menu") : t("Collapse menu")}
              className={`mt-1.5 w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs font-medium text-ink-tertiary hover:bg-slate-100 hover:text-ink-primary transition-colors ${railCollapsed ? "justify-center" : ""}`}>
              <M n={railCollapsed ? "chevron_right" : "chevron_left"} c="text-[17px]" />
              {!railCollapsed && <span>{t("Collapse")}</span>}
            </button>
          </nav>
        </aside>

        {/* NAV DRAWER (below 900px). Kept mounted so it can slide; `invisible` takes it out of
            the tab order and the accessibility tree while closed. */}
        <div className={`min-[900px]:hidden fixed inset-0 z-[75] ${navOpen ? "" : "invisible pointer-events-none"}`} aria-hidden={!navOpen}>
          <div className={`absolute inset-0 bg-ink-primary/40 transition-opacity duration-200 ${navOpen ? "opacity-100" : "opacity-0"}`} onClick={() => setNavOpen(false)} />
          <aside id="nav-drawer" role="dialog" aria-modal="true" aria-label={t("Main menu")}
            className={`absolute inset-y-0 start-0 w-[272px] max-w-[85vw] bg-sidebar border-e border-slate-200/80 shadow-clay-drawer flex flex-col transition-transform duration-200 ${navOpen ? "translate-x-0" : "-translate-x-full rtl:translate-x-full"}`}>
            <div className="flex items-start justify-between p-3 pb-0">
              {brand(false)}
              <button onClick={() => setNavOpen(false)} aria-label={t("Close menu")} className="w-10 h-10 -mt-1 rounded-lg flex items-center justify-center text-ink-tertiary hover:bg-slate-100 hover:text-ink-primary"><M n="close" c="text-lg" /></button>
            </div>
            <div className="px-3 pb-2"><LocationSwitcher block /></div>
            <nav className="px-3 pb-6 space-y-0.5 overflow-y-auto flex-1">{renderNav(false, () => setNavOpen(false))}</nav>
          </aside>
        </div>

        {/* MAIN */}
        <main className="app-main flex-1 flex flex-col min-w-0 bg-canvas overflow-hidden">
          <header className="h-[52px] bg-white/80 backdrop-blur-md border-b border-slate-200/80 px-2 sm:px-4 min-[1100px]:px-6 flex items-center justify-between gap-2 flex-shrink-0 z-10">
            <div className="flex items-center gap-1.5 sm:gap-4 min-w-0">
              <button onClick={() => setNavOpen(true)} aria-label={t("Open menu")} aria-expanded={navOpen} aria-controls="nav-drawer"
                className="min-[900px]:hidden w-10 h-10 shrink-0 rounded-lg flex items-center justify-center text-ink-secondary hover:bg-slate-100 hover:text-ink-primary"><M n="menu" c="text-xl" /></button>
              <h1 className="text-base font-bold text-ink-primary tracking-tight whitespace-nowrap truncate">{t(title)}</h1>
              <button onClick={() => setPalette(true)} aria-label={t("Search")} className="relative shrink-0 text-left group w-8 h-8 sm:h-auto sm:w-44 min-[1100px]:w-64">
                <M n="search" c="absolute left-2.5 top-2 text-ink-tertiary text-sm" />
                <div className="w-full h-8 sm:h-auto bg-slate-100/80 group-hover:bg-white text-xs pl-8 sm:pr-12 py-1.5 rounded-lg border border-slate-200/70 font-medium text-ink-placeholder whitespace-nowrap overflow-hidden text-ellipsis"><span className="hidden sm:inline">{t("Search jobs, customers…")}</span></div>
                <span className="hidden sm:inline absolute right-2 top-1.5 text-[11px] bg-white border border-slate-200 text-ink-tertiary font-mono px-1 rounded shadow-2xs">⌘K</span>
              </button>
            </div>
            <div className="flex items-center gap-1.5 sm:gap-3 shrink-0">
              <div className="hidden min-[900px]:block"><LocationSwitcher /></div>
              <ScanBox />
              {(me?.clock_attendance_enabled || me?.clock_wo_enabled) && <TimeClockChip me={me} />}
              <button onClick={() => setNotifOpen((o) => !o)} aria-label="Notifications" className="w-8 h-8 rounded-full border border-slate-200 bg-white flex items-center justify-center text-ink-secondary hover:text-ink-primary shadow-xs relative"><M n="notifications" c="text-sm" />{unread > 0 && <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-primary text-white text-[11px] font-bold flex items-center justify-center border-2 border-white">{unread > 9 ? "9+" : unread}</span>}</button>
              <div className="relative">
                <button onClick={() => setAvatarOpen((o) => !o)} aria-label={t("Account menu")} className="w-8 h-8 rounded-full bg-primary text-white font-semibold text-xs flex items-center justify-center shadow-xs hover:brightness-110 relative">{initials}{me?.mfa_required && !me?.mfa_enabled && <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-amber-400 border-2 border-white" />}</button>
                {avatarOpen && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setAvatarOpen(false)} />
                    <div className="absolute right-0 mt-2 w-56 bg-white rounded-xl border border-slate-200 shadow-drawer z-50 p-1.5 text-sm">
                      <div className="px-2.5 py-2 border-b border-slate-100 mb-1">
                        <p className="font-bold text-ink-primary truncate">{me?.name || "—"}</p>
                        <p className="text-[11px] text-ink-tertiary truncate">{me?.email || me?.role || ""}</p>
                      </div>
                      {can("can_view_own_payroll") && can("can_view_own_compensation") && (
                        <NavLink to="/my-earnings" onClick={() => setAvatarOpen(false)} className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-ink-secondary hover:bg-slate-100 hover:text-ink-primary">
                          <M n="payments" c="text-base" />{t("My earnings")}
                        </NavLink>
                      )}
                      <button onClick={() => { setDark((d) => !d); }} className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-ink-secondary hover:bg-slate-100 hover:text-ink-primary">
                        <M n={dark ? "light_mode" : "dark_mode"} c="text-base" />{dark ? t("Light mode") : t("Dark mode")}
                      </button>
                      <button onClick={() => setLang(lang === "en" ? "es" : lang === "es" ? "ar" : "en")} className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-ink-secondary hover:bg-slate-100 hover:text-ink-primary">
                        <span className="flex items-center gap-2.5"><M n="language" c="text-base" />{t("Language")}</span>
                        <span className="text-[11px] font-bold bg-slate-100 text-ink-tertiary rounded px-1 ">{lang}</span>
                      </button>
                      <button onClick={() => { setAvatarOpen(false); setSecOpen(true); }} className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-ink-secondary hover:bg-slate-100 hover:text-ink-primary">
                        <span className="flex items-center gap-2.5"><M n="lock" c="text-base" />{t("Security & sign-in")}</span>
                        {me?.mfa_required && !me?.mfa_enabled && <span className="w-2 h-2 rounded-full bg-amber-400" />}
                      </button>
                      <div className="my-1 border-t border-slate-100" />
                      <button onClick={() => { setAvatarOpen(false); signOut(); }} className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-ink-secondary hover:bg-slate-100 hover:text-ink-primary">
                        <M n="logout" c="text-base" />{t("Sign out")}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </header>
          {me?.mfa_required && !me?.mfa_enabled && (
            <button onClick={() => setSecOpen(true)} className="w-full text-left bg-amber-50 border-b border-amber-200 text-amber-800 text-xs font-semibold px-3 sm:px-6 py-2 flex items-center gap-2 hover:bg-amber-100">
              <M n="lock" c="text-sm" />Your organization requires two-factor authentication. <span className="underline">Set it up now</span> to keep access.
            </button>
          )}
          {children}
        </main>
      </div>
      {palette && <CommandPalette onClose={() => setPalette(false)} />}
      {notifOpen && <NotificationsPanel onClose={() => setNotifOpen(false)} onChanged={setUnread} />}
      {(secOpen || mfaGate) && <SecurityModal me={me} forced={mfaGate} onChanged={refresh}
        onClose={() => (mfaGate ? window.location.reload() : setSecOpen(false))} />}
    </div>
  );
}

// Fetches the authed QR SVG (/catalog/code.svg needs the bearer, so <img src> can't be used) and inlines it.
function QrSvg({ data }: { data: string }) {
  const [svg, setSvg] = useState("");
  useEffect(() => {
    let tok = ""; try { tok = localStorage.getItem("ros_tok") || ""; } catch { /* ignore */ }
    fetch(`/api/catalog/code.svg?type=qr&data=${encodeURIComponent(data)}`, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} })
      .then((r) => (r.ok ? r.text() : "")).then(setSvg).catch(() => setSvg(""));
  }, [data]);
  return <div className="w-44 h-44 bg-white rounded-xl border border-slate-200 p-2 flex items-center justify-center [&>svg]:w-full [&>svg]:h-full" dangerouslySetInnerHTML={{ __html: svg || "" }} />;
}

// Sign out: tell the server first (it records the sign-out), then drop the token whatever it answers.
async function signOut() {
  let tok = ""; try { tok = localStorage.getItem("ros_tok") || ""; } catch { /* ignore */ }
  if (tok) {
    try { await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${tok}` } }); }
    catch { /* offline: still sign out here */ }
  }
  try { localStorage.removeItem("ros_tok"); } catch { /* ignore */ }
  window.location.replace("/login");
}

// Two-factor enrollment / management. Reached from the top-bar avatar and the org-required banner.
function SecurityModal({ me, onClose, onChanged, forced = false }: { me: any; onClose: () => void; onChanged: () => void; forced?: boolean }) {
  const [enabled, setEnabled] = useState<boolean>(!!me?.mfa_enabled);
  const [step, setStep] = useState<"idle" | "enroll" | "done">("idle");
  const [enroll, setEnroll] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[]>([]);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<any>("/mfa/status").then((r) => setEnabled(!!r.enabled)).catch(() => {}); }, []);
  async function startEnroll() {
    setBusy(true); setErr("");
    try { const r = await api.post<any>("/mfa/enroll", {}); setEnroll(r); setStep("enroll"); }
    catch (e: any) { setErr(e.message || "Could not start enrollment"); } finally { setBusy(false); }
  }
  async function activate() {
    if (!code.trim()) return; setBusy(true); setErr("");
    try { const r = await api.post<any>("/mfa/activate", { code: code.trim() }); setRecovery(r.recovery_codes || []); setEnabled(true); setStep("done"); onChanged(); }
    catch (e: any) { setErr(e.message || "Invalid code"); } finally { setBusy(false); }
  }
  async function disable() {
    if (!pw) return; setBusy(true); setErr("");
    try { await api.post("/mfa/disable", { password: pw }); setEnabled(false); setStep("idle"); setPw(""); onChanged(); }
    catch (e: any) { setErr(e.message || "Could not disable"); } finally { setBusy(false); }
  }
  const inp = "glass-input w-full px-3 py-2 rounded-lg text-sm font-semibold text-ink-primary";
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={forced ? undefined : onClose} />
      <div className="relative w-full max-w-lg max-h-[90vh] flex flex-col bg-white rounded-2xl shadow-clay-drawer border border-slate-200/90 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
          <h3 className="text-sm font-bold text-ink-primary flex items-center gap-2"><M n="lock" c="text-base" />Security</h3>
          {forced
            ? <button onClick={signOut} className="text-[11px] font-semibold text-ink-tertiary hover:text-ink-primary">Sign out</button>
            : <button aria-label="Close" onClick={onClose} className="w-7 h-7 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>}
        </div>
        <div className="p-5 space-y-4 text-sm overflow-y-auto">
          <p className="text-[11px] font-bold uppercase tracking-wide text-ink-tertiary">Two-factor authentication</p>
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

          {enabled && step !== "done" && (
            <>
              <div className="flex items-center gap-2 text-emerald-700 font-semibold"><M n="check_circle" c="text-base" />Two-factor is ON for your account.</div>
              {me?.mfa_required
                ? <div className="text-xs text-ink-tertiary">Your organization requires 2FA, so it can't be turned off.</div>
                : <div className="space-y-2">
                    <label className="block text-[11px] font-bold text-ink-tertiary">Disable (confirm password)</label>
                    <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Account password" className={inp} />
                    <button onClick={disable} disabled={busy || !pw} className="px-3 py-2 rounded-lg bg-rose-600 text-white text-xs font-bold disabled:opacity-50">Turn off 2FA</button>
                  </div>}
            </>
          )}

          {!enabled && step === "idle" && (
            <>
              {forced && <div className="rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs px-3 py-2">Your organization requires two-factor authentication. Set it up to continue.</div>}
              <p className="text-ink-secondary text-xs">Protect your account with an authenticator app (Google Authenticator, Authy, 1Password…). You'll enter a 6-digit code at sign-in.</p>
              <button onClick={startEnroll} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold shadow-clay-sm disabled:opacity-50">{busy ? "…" : "Enable two-factor"}</button>
            </>
          )}

          {!enabled && step === "enroll" && enroll && (
            <>
              <p className="text-ink-secondary text-xs">1. Scan this QR in your authenticator app, or enter the key manually.</p>
              <div className="flex flex-col items-center gap-2">
                <QrSvg data={enroll.otpauth_uri} />
                <code className="text-[11px] font-mono bg-slate-100 rounded px-2 py-1 break-all text-ink-secondary">{enroll.secret}</code>
              </div>
              <p className="text-ink-secondary text-xs">2. Enter the 6-digit code it shows:</p>
              <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" placeholder="123456" className={`${inp} font-mono  text-center`} />
              <button onClick={activate} disabled={busy || code.length < 6} className="w-full px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold shadow-clay-sm disabled:opacity-50">{busy ? "Verifying…" : "Verify & turn on"}</button>
            </>
          )}

          {step === "done" && (
            <>
              <div className="flex items-center gap-2 text-emerald-700 font-semibold"><M n="check_circle" c="text-base" />Two-factor is now on.</div>
              {recovery.length > 0 && (
                <div>
                  <p className="text-xs text-ink-secondary mb-1">Save these recovery codes somewhere safe — they're shown once and let you back in if you lose your device:</p>
                  <div className="grid grid-cols-2 gap-1.5 bg-slate-50 rounded-lg border border-slate-200 p-3 tabular-nums text-xs">
                    {recovery.map((c) => <span key={c} className="text-ink-primary">{c}</span>)}
                  </div>
                </div>
              )}
              <button onClick={onClose} className="w-full px-4 py-2 rounded-lg bg-ink-primary text-white text-xs font-bold">Done</button>
            </>
          )}
          {!forced && (
            <div className="border-t border-slate-100 pt-4">
              <p className="text-[11px] font-bold uppercase tracking-wide text-ink-tertiary">Signed-in devices</p>
              <p className="mt-0.5 mb-1 text-xs text-ink-tertiary">Don't recognise one? Sign it out and change your password.</p>
              <DeviceList base="/auth/sessions" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── ⌘K command palette: jump to modules + live order/client search ───────── */
function CommandPalette({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const { can } = useMe();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ label: string; sub: string; to: string; icon: string }[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);

  // Respect permissions like the sidebar — don't offer jumps to modules the user can't see.
  const commands = NAV.filter((n) => can(NAV_PERM[n.to])).map((n) => ({ label: n.l, sub: "Go to " + n.l, to: n.to, icon: n.i }));
  const filteredCmds = commands.filter((c) => !q || c.label.toLowerCase().includes(q.toLowerCase()));

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    let live = true;
    const t = setTimeout(async () => {
      const out: typeof hits = [];
      try {
        const o = await api.get<{ items: any[] }>(`/orders?q=${encodeURIComponent(q)}&limit=5`);
        (o.items || []).forEach((r: any) => out.push({ label: `${r.order_number || r.code || "WO"} · ${r.client_name || r.client?.name || "—"}`, sub: r.device_summary || r.status || "Work order", to: "/orders", icon: "handyman" }));
      } catch { /* ignore */ }
      try {
        const c = await api.get<{ items: any[] }>(`/clients?q=${encodeURIComponent(q)}&limit=5`);
        (c.items || []).forEach((r: any) => out.push({ label: r.name || "Contact", sub: r.phone || r.email || "Client", to: "/clients", icon: "group" }));
      } catch { /* ignore */ }
      if (live) setHits(out);
    }, 220);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  const go = (to: string) => { onClose(); nav(to); };

  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center pt-[12vh]" onClick={onClose}>
      <div className="absolute inset-0 bg-ink-primary/30 backdrop-blur-[2px]" />
      <div className="relative w-[560px] max-w-[92vw] bg-white rounded-2xl shadow-drawer border border-slate-200 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-100">
          <M n="search" c="text-ink-tertiary text-base" />
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Search or jump to…")} className="flex-1 text-sm outline-none bg-transparent placeholder-ink-placeholder" />
          <span className="text-[11px] text-ink-tertiary tabular-nums border border-slate-200 rounded px-1">Esc</span>
        </div>
        <div className="max-h-[52vh] overflow-y-auto p-2">
          {hits.length > 0 && (
            <div className="mb-2">
              <div className="px-2 py-1 text-[11px] font-bold text-ink-tertiary ">{t("Results")}</div>
              {hits.map((h, i) => (
                <button key={i} onClick={() => go(h.to)} className="w-full flex items-center gap-3 px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-left">
                  <M n={h.icon} c="text-sm text-primary" /><div className="min-w-0"><div className="text-sm font-semibold text-ink-primary truncate">{h.label}</div><div className="text-[11px] text-ink-tertiary truncate">{h.sub}</div></div>
                </button>
              ))}
            </div>
          )}
          <div className="px-2 py-1 text-[11px] font-bold text-ink-tertiary ">{t("Navigate")}</div>
          {filteredCmds.map((c) => (
            <button key={c.to} onClick={() => go(c.to)} className="w-full flex items-center gap-3 px-2.5 py-2 rounded-lg hover:bg-slate-100 text-left">
              <M n={c.icon} c="text-sm text-ink-tertiary" /><span className="text-sm font-medium text-ink-primary">{c.label}</span>
            </button>
          ))}
          {filteredCmds.length === 0 && hits.length === 0 && <div className="px-3 py-6 text-center text-xs text-ink-tertiary">No matches.</div>}
        </div>
      </div>
    </div>
  );
}

/* ── Notifications feed popover ───────────────────────────────────────────── */
function NotificationsPanel({ onClose, onChanged }: { onClose: () => void; onChanged: (n: number) => void }) {
  const nav = useNavigate();
  const [items, setItems] = useState<any[] | null>(null);
  function load() { api.get<{ items: any[] }>("/tech/notifications").then((r) => setItems(r.items || [])).catch(() => setItems([])); }
  useEffect(() => { load(); }, []);
  const unreadCount = (items || []).filter((n) => !n.read).length;
  useEffect(() => { if (items) onChanged(unreadCount); }, [items]);

  async function markAll() { try { await api.post("/tech/notifications/read-all", {}); load(); } catch { /* ignore */ } }
  async function clearAll() { setItems([]); try { await api.post("/tech/notifications/dismiss-all", {}); } catch { /* ignore */ } load(); }
  async function dismiss(n: any, e: React.MouseEvent) {
    e.stopPropagation();
    setItems((cur) => (cur || []).filter((x) => x.id !== n.id));  // optimistic
    try { await api.post(`/tech/notifications/${n.id}/dismiss`, {}); } catch { load(); }
  }
  async function open(n: any) {
    if (!n.read) { try { await api.post(`/tech/notifications/${n.id}/read`, {}); } catch { /* ignore */ } }
    onClose();
    if (n.deep_link) nav(toAppPath(n.deep_link));
  }

  async function enablePush() {
    try {
      const cfg = await api.get<any>("/push/vapid-public-key");
      if (!cfg.enabled || !cfg.public_key) { await alertDialog("Push isn't configured on this server."); return; }
      if (Notification.permission !== "granted") { const p = await Notification.requestPermission(); if (p !== "granted") { await alertDialog("Notifications were blocked."); return; } }
      // The site's service worker only handles push here (it leaves the main app's pages and API
      // alone); register it if this browser never has, or `ready` would wait forever.
      if (!("serviceWorker" in navigator)) { await alertDialog("This browser can't receive push notifications."); return; }
      if (!(await navigator.serviceWorker.getRegistration("/"))) await navigator.serviceWorker.register("/sw.js");
      const reg = await navigator.serviceWorker.ready;
      const b64 = (s: string) => { const pad = "=".repeat((4 - (s.length % 4)) % 4); const r = (s + pad).replace(/-/g, "+").replace(/_/g, "/"); const raw = atob(r); return Uint8Array.from([...raw].map((c) => c.charCodeAt(0))); };
      const sub: any = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(cfg.public_key) });
      const j = sub.toJSON();
      await api.post("/push/subscribe", { endpoint: j.endpoint, keys: { p256dh: j.keys.p256dh, auth: j.keys.auth } });
      await alertDialog("Push notifications enabled on this device.");
    } catch (e: any) { await alertDialog(e.message || "Could not enable push"); }
  }
  return (
    <div className="fixed inset-0 z-[80]" onClick={onClose}>
      <div className="absolute right-2 sm:right-5 top-[54px] w-[360px] max-w-[92vw] bg-white rounded-2xl shadow-drawer border border-slate-200 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <h3 className="text-sm font-bold text-ink-primary">{t("Notifications")}</h3>
          <div className="flex items-center gap-2">
            <button onClick={enablePush} title="Enable browser push on this device" className="text-[11px] font-semibold text-ink-secondary hover:text-primary flex items-center gap-0.5"><M n="notifications" c="text-xs" />Push</button>
            {unreadCount > 0 && <button onClick={markAll} className="text-[11px] font-semibold text-primary hover:underline">{t("Mark all read")}</button>}
            {(items?.length || 0) > 0 && <button onClick={clearAll} className="text-[11px] font-semibold text-ink-tertiary hover:text-rose-600 hover:underline">{t("Clear all")}</button>}
          </div>
        </div>
        <div className="max-h-[60vh] overflow-y-auto divide-y divide-slate-100">
          {items == null && <div className="px-4 py-6 text-xs text-ink-tertiary">Loading…</div>}
          {items?.length === 0 && <div className="px-4 py-10 text-center"><M n="notifications" c="mx-auto mb-2 text-2xl text-ink-placeholder" /><div className="text-xs text-ink-tertiary mt-2">{t("You're all caught up.")}</div></div>}
          {items?.map((n) => (
            <div key={n.id} className={`group relative w-full hover:bg-blue-50/50 ${n.read ? "" : "bg-blue-50/30"}`}>
              <button onClick={() => open(n)} className="w-full text-left px-4 py-3 pr-9 flex gap-3">
                <span className={`mt-1 w-1.5 h-1.5 rounded-full flex-shrink-0 ${n.read ? "bg-transparent" : "bg-primary"}`} />
                <div className="min-w-0"><div className="text-xs font-bold text-ink-primary">{n.title || n.kind || "Update"}</div>{n.body && <div className="text-[11px] text-ink-secondary mt-0.5 line-clamp-2">{n.body}</div>}<div className="text-[11px] text-ink-tertiary mt-1">{fmtServerTime(n.created_at)}</div></div>
              </button>
              <button onClick={(e) => dismiss(n, e)} title={t("Dismiss")} aria-label={t("Dismiss")}
                className="absolute top-2 right-2 w-6 h-6 rounded-md flex items-center justify-center text-ink-placeholder hover:text-rose-600 hover:bg-rose-50 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity">
                <M n="close" c="text-sm" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Top-bar time clock (attendance shift + running job timer) ──────────────
   React port of legacy TimeClock.mount(). Gated by the org clock flags in the
   Shell header. Endpoints: /timeclock/shift/active, /shift/clock-in|out, /active. */
function fmtHMS(sec: number) {
  const s = Math.max(0, Math.floor(sec));
  const h = String(Math.floor(s / 3600)).padStart(2, "0");
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${h}:${m}:${ss}`;
}
/* Phase 2 — universal scan box. A USB keyboard-wedge scanner (or typed code) hits
   Enter; we resolve it and jump straight to the matched WO / product / asset. */
function ScanBox() {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const nav = useNavigate();
  const ref = useRef<HTMLInputElement | null>(null);
  useEffect(() => { if (open) ref.current?.focus(); }, [open]);
  async function submit() {
    const code = val.trim();
    if (!code) return;
    setMsg(null);
    try {
      const r = await api.post<any>("/scan/resolve", { code });
      if (r?.matched) { setVal(""); setOpen(false); nav(`${r.route}?open=${r.id}`); }
      else setMsg("No match");
    } catch { setMsg("Scan failed"); }
  }
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title="Scan (barcode / IMEI / order)" aria-label="Scan"
        className="w-8 h-8 rounded-full border border-slate-200 bg-white flex items-center justify-center text-ink-secondary hover:text-ink-primary shadow-xs"><M n="qr_code_scanner" c="text-sm" /></button>
      {open && (
        <div className="absolute right-0 mt-2 w-64 bg-raised rounded-xl border border-slate-200/80 shadow-clay-card z-50 p-2.5">
          <div className="text-[11px] font-bold text-ink-tertiary mb-1.5">Scan or type a code</div>
          <input ref={ref} value={val} onChange={(e) => { setVal(e.target.value); setMsg(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") setOpen(false); }}
            placeholder="WO-00042, IMEI, SKU…" className="glass-input rounded-lg px-3 py-2 text-sm w-full font-mono" />
          {msg && <div className="text-[11px] text-rose-600 mt-1.5">{msg} — try the ⌘K search.</div>}
          <div className="text-[11px] text-ink-tertiary mt-1.5">Jumps to the work order, product, or asset.</div>
        </div>
      )}
    </div>
  );
}

function TimeClockChip({ me }: { me: any }) {
  const [shift, setShift] = useState<any>(null);   // {elapsed_sec, open} | null
  const [job, setJob] = useState<any>(null);       // {duration_sec, order_number, category} | null
  const [tick, setTick] = useState(0);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const attendance = me?.clock_attendance_enabled;
  const woClock = me?.clock_wo_enabled;
  const load = () => {
    if (attendance) api.get<any>("/timeclock/shift/active").then((r) => setShift(r.shift || null)).catch(() => {});
    if (woClock) api.get<any>("/timeclock/active").then((r) => setJob(r.active || null)).catch(() => {});
  };
  useVisibleInterval(load, 30000);
  useEffect(() => { const id = setInterval(() => setTick((t) => t + 1), 1000); return () => clearInterval(id); }, []);
  // base elapsed + local ticks since last fetch (approx live display)
  const shiftSec = shift?.open ? (shift.elapsed_sec || 0) + tick : 0;
  const jobSec = job?.running !== false && job ? (job.duration_sec || 0) + tick : 0;
  async function clockIn() { setBusy(true); try { await api.post("/timeclock/shift/clock-in", {}); load(); } catch (e: any) { await alertDialog(e.message); } finally { setBusy(false); } }
  async function clockOut() { setBusy(true); try { await api.post("/timeclock/shift/clock-out", {}); setShift(null); load(); } catch (e: any) { await alertDialog(e.message); } finally { setBusy(false); } }
  // Job/labour clock on/off. A general timer (no WO) uses category "other"; job/task
  // timers are started from a work order or task and stopped from here or there.
  async function startJob() { setBusy(true); try { await api.post("/timeclock/start", { category: "other", note: "General" }); load(); } catch (e: any) { await alertDialog(e.message); } finally { setBusy(false); } }
  async function stopJob() { setBusy(true); try { await api.post("/timeclock/stop", {}); setJob(null); load(); } catch (e: any) { await alertDialog(e.message); } finally { setBusy(false); } }
  const onShift = !!shift?.open;
  // What the running job is called — so the chip answers "which job am I on?"
  const jobName = job ? (job.order_number || job.task_title || (job.category === "task" ? "Task" : "General")) : null;
  const running = onShift || !!job;
  // Idle reads as an action ("Clock in"), not a dead 00:00:00; running reads as a
  // status (what you're timing + live elapsed).
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title={jobName ? `Timing: ${jobName}` : t("Time clock")} aria-label={running ? (jobName ? `Timing: ${jobName}` : t("Time clock")) : t("Clock in")}
        className={`flex items-center gap-1.5 h-8 px-2.5 rounded-full border text-xs font-semibold shadow-xs max-w-[220px] ${running ? "bg-emerald-50 border-emerald-200 text-emerald-700" : "bg-white border-slate-200 text-ink-secondary hover:text-ink-primary"}`}>
        {running
          ? <>
              <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-emerald-500" />
              {jobName && <span className="hidden sm:inline truncate max-w-[96px]">{jobName}</span>}
              <span className="tabular-nums shrink-0">{job ? fmtHMS(jobSec) : fmtHMS(shiftSec)}</span>
            </>
          : <><M n="play_arrow" c="text-sm" /><span className="hidden sm:inline">{t("Clock in")}</span></>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-2 w-64 bg-white rounded-xl border border-slate-200 shadow-drawer z-50 p-3 space-y-3">
            {woClock && (
              <div className="rounded-lg bg-slate-50 border border-slate-200 p-2.5">
                <div className="text-[11px] font-bold text-ink-tertiary mb-1">Job timer</div>
                {job ? (
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-emerald-700 truncate mr-2">{jobName} · <span className="tabular-nums">{fmtHMS(jobSec)}</span></span>
                    <button onClick={stopJob} disabled={busy} className="px-2.5 py-1 rounded-lg bg-rose-600 text-white text-[11px] font-semibold disabled:opacity-60">Stop</button>
                  </div>
                ) : (
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] text-ink-tertiary">Not running</span>
                    <button onClick={startJob} disabled={busy} className="px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-semibold disabled:opacity-60">Start</button>
                  </div>
                )}
              </div>
            )}
            {attendance && (
              <div className="rounded-lg bg-slate-50 border border-slate-200 p-2.5">
                <div className="text-[11px] font-bold text-ink-tertiary mb-1">Shop attendance</div>
                {onShift ? (
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-emerald-700 tabular-nums">{fmtHMS(shiftSec)}</span>
                    <button onClick={clockOut} disabled={busy} className="px-2.5 py-1 rounded-lg bg-rose-600 text-white text-[11px] font-semibold disabled:opacity-60">Clock out</button>
                  </div>
                ) : (
                  <button onClick={clockIn} disabled={busy} className="w-full px-2.5 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">Clock in</button>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ── Top-bar branch switcher (legacy App.mountLocationSwitcher). Sets the global
   API location scope; changing it reloads so every list re-fetches scoped. ── */
function LocationSwitcher({ block = false }: { block?: boolean }) {
  const [locs, setLocs] = useState<{ id: string; name: string }[]>([]);
  const [sel, setSel] = useState<string>(getLocationScope());
  useEffect(() => {
    api.get<any>("/staff/locations").then((r) => {
      const active = (r.items || r || []).filter((l: any) => l.active !== false);
      setLocs(active);
      // A remembered branch that no longer exists for this account (deleted, deactivated, or a
      // different login on this browser) silently filtered every list to nothing while the
      // switcher read "All locations" — and single-branch shops have no switcher to undo it.
      const cur = getLocationScope();
      if (cur && !active.some((l: any) => l.id === cur)) { setLocationScope(""); window.location.reload(); }
    }).catch(() => {});
  }, []);
  if (locs.length < 2) return null; // single branch: no switcher needed
  return (
    <select value={sel} onChange={(e) => { setSel(e.target.value); setLocationScope(e.target.value); window.location.reload(); }}
      title="Location" aria-label={t("Location")} className={`rounded-full border border-slate-200 bg-white text-xs font-semibold text-ink-secondary px-2 shadow-xs ${block ? "h-10 w-full" : "h-8 max-w-[140px]"}`}>
      <option value="">All locations</option>
      {locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
    </select>
  );
}
