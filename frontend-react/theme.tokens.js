/* RepairOS colour tokens — one palette, two modes.
 *
 * Every neutral surface, border and text colour, the primary, and the soft accent tints
 * resolve through a CSS variable (an "r g b" triplet, so Tailwind's /alpha modifiers keep
 * working). Light values are exactly the colours the app used before, so light mode is
 * unchanged. Dark values come from the design council (Stitch / Material 3 tonal surfaces,
 * Linear-style hairlines, and an accessibility pass):
 *   - elevation = a lighter surface, never a bigger shadow (canvas < card < raised)
 *   - every neutral carries the brand's navy hue; nothing is pure black or pure white
 *   - the primary is a fill for buttons (#2c72d9, Stitch's primary-container) and a lighter
 *     blue for text, links, focus and the active nav item
 *   - soft tints are a dark fill of the hue with light text of the same hue
 */
import colors from "tailwindcss/colors";

const hex = (h) => {
  const s = h.replace("#", "");
  const n = parseInt(s.length === 3 ? s.split("").map((c) => c + c).join("") : s, 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
};

// name -> [light, dark]
export const NEUTRALS = {
  canvas: ["#f7f8fd", "#0e1119"],
  sidebar: ["#ffffff", "#0b0e15"],
  card: ["#ffffff", "#151a24"],
  raised: ["#ffffff", "#1b2130"],
  "slate-50": [colors.slate[50], "#181d29"],
  "slate-100": [colors.slate[100], "#1f2534"],
  "slate-200": [colors.slate[200], "#283043"],
  "slate-300": [colors.slate[300], "#353e53"],
  // borders (slate-100 / 200 / 300 as lines)
  "line-100": [colors.slate[100], "#1f2534"],
  "line-200": [colors.slate[200], "#2a3143"],
  "line-300": [colors.slate[300], "#4a5878"],
  // text
  "ink-primary": ["#14213f", "#e4e8f2"],
  "ink-secondary": ["#435577", "#aab4c9"],
  "ink-tertiary": ["#6c7a99", "#8e9ab4"],
  "ink-placeholder": ["#9aa7c4", "#76829d"],
  "text-slate-400": [colors.slate[400], "#76829d"],
  "text-slate-500": [colors.slate[500], "#8e9ab4"],
  "text-slate-600": [colors.slate[600], "#aab4c9"],
  "text-slate-700": [colors.slate[700], "#c3cbdb"],
  "text-slate-800": [colors.slate[800], "#d6dce8"],
  // primary
  "primary-fill": ["#0059ba", "#2c72d9"],
  "primary-text": ["#0059ba", "#93b8ff"],
  "primary-container": ["#2c72d9", "#3a80e8"],
  // the dark neutral button (bg-ink-primary) stays a dark button with white text
  "btn-neutral": ["#14213f", "#33415f"],
};

// Soft accent tints: [bg-50, bg-100, border-100/200, border-300, text] in dark;
// light uses Tailwind's own shades.
const DARK_TINTS = {
  blue:    { bg50: "#1a2a47", bg100: "#203457", line: "#2a4170", line3: "#36528a", text: "#9cc0ff" },
  sky:     { bg50: "#13304a", bg100: "#183b59", line: "#22506f", line3: "#2c6488", text: "#8fd3ff" },
  indigo:  { bg50: "#1f2448", bg100: "#262c58", line: "#333b73", line3: "#414a8c", text: "#aab4ff" },
  violet:  { bg50: "#2a2347", bg100: "#322a57", line: "#433871", line3: "#54468a", text: "#c7b2ff" },
  purple:  { bg50: "#2a2347", bg100: "#322a57", line: "#433871", line3: "#54468a", text: "#c7b2ff" },
  emerald: { bg50: "#12332c", bg100: "#173f36", line: "#1f5547", line3: "#296b5a", text: "#6ee0b8" },
  green:   { bg50: "#133220", bg100: "#183e28", line: "#215436", line3: "#2b6a45", text: "#7fdc9e" },
  teal:    { bg50: "#10322f", bg100: "#143e3a", line: "#1c544e", line3: "#256a62", text: "#6ad8c8" },
  amber:   { bg50: "#352a14", bg100: "#423418", line: "#5a4620", line3: "#725a29", text: "#f2c46b" },
  yellow:  { bg50: "#352f12", bg100: "#423a16", line: "#5a4f1d", line3: "#726425", text: "#f0d36a" },
  orange:  { bg50: "#38240f", bg100: "#462d13", line: "#5f3e1a", line3: "#784e22", text: "#f7b267" },
  rose:    { bg50: "#3a1d27", bg100: "#47232f", line: "#623041", line3: "#7c3d52", text: "#ff8b9c" },
  red:     { bg50: "#3d1c1f", bg100: "#4a2226", line: "#662f34", line3: "#803b41", text: "#ff9a9a" },
  pink:    { bg50: "#3a1c30", bg100: "#47223a", line: "#622f50", line3: "#7c3b65", text: "#ffa3d1" },
};

export function cssVars() {
  const light = {}, dark = {};
  for (const [k, [l, d]] of Object.entries(NEUTRALS)) { light[`--c-${k}`] = hex(l); dark[`--c-${k}`] = hex(d); }
  for (const [fam, t] of Object.entries(DARK_TINTS)) {
    const c = colors[fam];
    const set = (k, l, d) => { light[`--c-${fam}-${k}`] = hex(l); dark[`--c-${fam}-${k}`] = hex(d); };
    set("bg50", c[50], t.bg50); set("bg100", c[100], t.bg100);
    set("line100", c[100], t.line); set("line200", c[200], t.line); set("line300", c[300], t.line3);
    set("t500", c[500], t.text); set("t600", c[600], t.text); set("t700", c[700], t.text); set("t800", c[800], t.text); set("t900", c[900], t.text);
  }
  // Shadows: soft clay in light; in dark depth comes from surfaces, so shadows go quiet.
  light["--sh-sm"] = "0 2px 8px -1px rgba(20,33,63,.06), inset 0 1px 0 rgba(255,255,255,.95)";
  light["--sh-card"] = "0 12px 30px -4px rgba(20,33,63,.08), inset 0 1px 0 rgba(255,255,255,.95)";
  light["--sh-drawer"] = "-16px 0 48px -6px rgba(15,28,60,.18), inset 1px 0 0 rgba(255,255,255,.9)";
  light["--sh-menu"] = "0 12px 30px -4px rgba(20,33,63,.12)";
  light["--scrim"] = "20 33 63";
  dark["--sh-sm"] = "inset 0 1px 0 rgba(255,255,255,.03)";
  dark["--sh-card"] = "inset 0 1px 0 rgba(255,255,255,.03)";
  dark["--sh-drawer"] = "-16px 0 48px -8px rgba(0,0,0,.6), inset 1px 0 0 rgba(255,255,255,.05)";
  dark["--sh-menu"] = "0 8px 24px -4px rgba(0,0,0,.55), 0 0 0 1px rgb(42 49 67)";
  dark["--scrim"] = "5 8 14";
  return { light, dark };
}

const v = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

/** Per-utility colour overrides so `bg-white` (a surface) and `text-white` (on a button)
 *  can mean different things, and slate/accents follow the theme. */
export function themeColors() {
  const bg = { white: v("card"), canvas: v("canvas"), sidebar: v("sidebar"), raised: v("raised"),
    "ink-primary": v("btn-neutral"), primary: v("primary-fill"), "primary-container": v("primary-container"),
    slate: { 50: v("slate-50"), 100: v("slate-100"), 200: v("slate-200"), 300: v("slate-300") } };
  const border = { slate: { 100: v("line-100"), 200: v("line-200"), 300: v("line-300") }, primary: v("primary-text") };
  const text = { "ink-primary": v("ink-primary"), "ink-secondary": v("ink-secondary"), "ink-tertiary": v("ink-tertiary"),
    "ink-placeholder": v("ink-placeholder"), primary: v("primary-text"),
    slate: { 400: v("text-slate-400"), 500: v("text-slate-500"), 600: v("text-slate-600"), 700: v("text-slate-700"), 800: v("text-slate-800") } };
  const ring = { white: v("card"), slate: { 100: v("line-100"), 200: v("line-200"), 300: v("line-300") }, primary: v("primary-text") };
  for (const fam of Object.keys(DARK_TINTS)) {
    bg[fam] = { 50: v(`${fam}-bg50`), 100: v(`${fam}-bg100`) };
    border[fam] = { 100: v(`${fam}-line100`), 200: v(`${fam}-line200`), 300: v(`${fam}-line300`) };
    text[fam] = { 500: v(`${fam}-t500`), 600: v(`${fam}-t600`), 700: v(`${fam}-t700`), 800: v(`${fam}-t800`), 900: v(`${fam}-t900`) };
  }
  return { bg, border, text, ring };
}
