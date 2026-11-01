// Tekly Go is light unless the person picks Dark (or "Same as phone") in More.
// The web app's dark palette hangs off the `.dark` class (theme.tokens.js), so we toggle that.
export type ThemePref = "system" | "light" | "dark";
const KEY = "go_theme";

export function getTheme(): ThemePref {
  try { const v = localStorage.getItem(KEY); return v === "dark" || v === "system" ? v : "light"; } catch { return "light"; }
}

const mq = () => window.matchMedia("(prefers-color-scheme: dark)");

export function applyTheme(pref: ThemePref = getTheme()) {
  const dark = pref === "dark" || (pref === "system" && mq().matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0e1119" : "#f7f8fd");
}

export function setTheme(pref: ThemePref) {
  try { if (pref === "light") localStorage.removeItem(KEY); else localStorage.setItem(KEY, pref); } catch { /* ignore */ }
  applyTheme(pref);
}

export function watchSystemTheme() {
  mq().addEventListener("change", () => { if (getTheme() === "system") applyTheme("system"); });
}
