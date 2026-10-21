// Lightweight i18n. ponytail: flat dict + localStorage; switching language reloads the
// page instead of wiring a React context/provider. Upgrade path: move to react-i18next
// if plural rules, interpolation, or live switching without reload become needed.
export type Lang = "en" | "es" | "ar";
export const LANGS: { code: Lang; label: string }[] = [
  { code: "en", label: "English" },
  { code: "es", label: "Español" },
  { code: "ar", label: "العربية" },
];

const DICT: Record<Lang, Record<string, string>> = {
  en: {},
  es: {
    "My company": "Mi empresa", "Inquiries": "Consultas", "Tasks": "Tareas",
    "Appointments": "Citas", "Chats": "Chats", "Work orders": "Órdenes de trabajo",
    "Contacts": "Contactos", "Sales": "Ventas", "Invoices": "Facturas",
    "Inventory": "Inventario", "Finance": "Finanzas", "Reports": "Informes",
    "Settings": "Ajustes", "Workshop Central": "Central del taller",
    "Search jobs, customers…": "Buscar trabajos, clientes…",
    "Notifications": "Notificaciones", "You're all caught up.": "Estás al día.",
    "Mark all read": "Marcar todo leído", "Navigate": "Navegar", "Results": "Resultados",
    "Search or jump to…": "Buscar o ir a…", "Light": "Claro", "Dark": "Oscuro",
    "Language": "Idioma",
    "Front desk": "Recepción", "Workshop": "Taller", "Commerce": "Comercio",
    "Grow": "Crecimiento", "Admin": "Administración",
    "Home": "Inicio", "My work": "Mi trabajo", "Catalog": "Catálogo",
    "Collapse": "Contraer", "Light mode": "Modo claro", "Dark mode": "Modo oscuro",
    "Clock in": "Fichar entrada", "Security & two-factor": "Seguridad y doble factor", "Sign out": "Cerrar sesión", "My earnings": "Mis ganancias",
  },
  ar: {
    "My company": "شركتي", "Inquiries": "الاستفسارات", "Tasks": "المهام",
    "Appointments": "مواعيد", "Chats": "المحادثات", "Work orders": "أوامر العمل",
    "Contacts": "جهات الاتصال", "Sales": "المبيعات", "Invoices": "الفواتير",
    "Inventory": "المخزون", "Finance": "المالية", "Reports": "التقارير",
    "Settings": "الإعدادات", "Workshop Central": "مركز الورشة",
    "Front desk": "الاستقبال", "Workshop": "الورشة", "Commerce": "التجارة",
    "Grow": "النمو", "Admin": "الإدارة", "Collapse": "طيّ",
    "Notifications": "الإشعارات", "Language": "اللغة",
    "Light mode": "الوضع الفاتح", "Dark mode": "الوضع الداكن",
    "Clock in": "تسجيل الدخول", "Security & two-factor": "الأمان والتحقق بخطوتين", "Sign out": "تسجيل الخروج", "My earnings": "أرباحي",
  },
};

// BCP-47 locale used for number/date/currency formatting (Intl). Region comes
// later from org settings; language gives a safe default per market.
const LOCALE: Record<Lang, string> = { en: "en-US", es: "es-ES", ar: "ar" };

export function getLang(): Lang {
  try { return (localStorage.getItem("ros_lang") as Lang) || "en"; } catch { return "en"; }
}
export function getLocale(): string { return LOCALE[getLang()] || "en-US"; }
export function getDir(): "ltr" | "rtl" { return getLang() === "ar" ? "rtl" : "ltr"; }
// Apply text direction to the document — called on load and before a lang switch.
export function applyDir() { try { document.documentElement.dir = getDir(); document.documentElement.lang = getLang(); } catch { /* ignore */ } }
export function setLang(l: Lang) {
  try { localStorage.setItem("ros_lang", l); } catch { /* ignore */ }
  applyDir();
  location.reload();
}
export function t(s: string): string {
  const l = getLang();
  return (DICT[l] && DICT[l][s]) || s;
}
