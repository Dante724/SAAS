// Tekly Go follows the phone's light/dark setting unless the person picks one in More.
// The web app's dark palette hangs off the `.dark` class (theme.tokens.js), so we toggle that.
export type ThemePref = "system" | "light" | "dark";
const KEY = "go_theme";

export function getTheme(): ThemePref {
  try { const v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : "system"; } catch { return "system"; }
}

const mq = () => window.matchMedia("(prefers-color-scheme: dark)");

export function applyTheme(pref: ThemePref = getTheme()) {
  const dark = pref === "dark" || (pref === "system" && mq().matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

export function setTheme(pref: ThemePref) {
  try { if (pref === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, pref); } catch { /* ignore */ }
  applyTheme(pref);
}

export function watchSystemTheme() {
  mq().addEventListener("change", () => { if (getTheme() === "system") applyTheme("system"); });
}
