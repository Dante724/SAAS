/** Tekly — "Liquid Clay Studio" theme, ported verbatim from the Stitch export. */
import plugin from "tailwindcss/plugin";
import { cssVars, themeColors } from "./theme.tokens.js";

const T = themeColors();
const { light, dark } = cssVars();

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: "#faf8ff",
        "surface-dim": "#cdd9ff",
        "surface-bright": "#faf8ff",
        "surface-container-lowest": "#ffffff",
        "surface-container-low": "#f2f3ff",
        "surface-container": "#eaedff",
        "surface-container-high": "#e2e7ff",
        "surface-container-highest": "#dae2ff",
        "surface-variant": "#dae2ff",
        outline: "#727784",
        "outline-variant": "#c2c6d5",
        primary: "#0059ba",
        "primary-container": "#2c72d9",
        "on-primary": "#ffffff",
        "on-primary-container": "#fefcff",
        secondary: "#6848be",
        "secondary-container": "#a484fe",
        tertiary: "#00685d",
        "ink-primary": "#14213f",
        "ink-secondary": "#435577",
        "ink-tertiary": "#6c7a99",
        "ink-placeholder": "#9aa7c4",
        "status-new-bg": "rgba(79, 142, 247, 0.15)", "status-new-ink": "#326fd1",
        "status-diagnosed-bg": "rgba(44, 212, 192, 0.15)", "status-diagnosed-ink": "#0a9887",
        "status-waiting-bg": "rgba(251, 191, 86, 0.2)", "status-waiting-ink": "#b7791f",
        "status-repair-bg": "rgba(168, 85, 247, 0.15)", "status-repair-ink": "#8027cf",
        "status-ready-bg": "rgba(74, 222, 128, 0.18)", "status-ready-ink": "#1e8a44",
        "status-done-bg": "rgba(34, 197, 94, 0.18)", "status-done-ink": "#15803d",
        "status-overdue-bg": "rgba(251, 113, 133, 0.15)", "status-overdue-ink": "#be123c",
        "status-draft-bg": "rgba(108, 122, 153, 0.14)", "status-draft-ink": "#435577",
      },
      // Surfaces, borders, text, primary and soft tints resolve through theme tokens
      // (theme.tokens.js): `bg-white` is a surface, `text-white` stays white.
      backgroundColor: T.bg,
      borderColor: T.border,
      textColor: T.text,
      ringColor: T.ring,
      fontFamily: {
        sans: ["Geist", "Inter", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "JetBrains Mono", "ui-monospace", "monospace"],
      },
      // Tekly Go's five type steps (size/line-height): large title, title, body, subhead, caption.
      fontSize: {
        "go-display": ["28px", { lineHeight: "34px" }],
        "go-title": ["20px", { lineHeight: "25px" }],
        "go-body": ["16px", { lineHeight: "22px" }],
        "go-sub": ["14px", { lineHeight: "19px" }],
        "go-caption": ["12px", { lineHeight: "16px" }],
      },
      boxShadow: {
        // Shadows follow the theme (soft clay in light; quiet in dark, where surfaces carry depth).
        "clay-sm": "var(--sh-sm)",
        "clay-card": "var(--sh-card)",
        "clay-drawer": "var(--sh-drawer)",
        drawer: "var(--sh-menu)",   // used by menus and popovers
        menu: "var(--sh-menu)",
        "specular-inset": "inset 0 1px 1.5px rgba(255, 255, 255, 0.9), inset 0 -1px 1px rgba(0, 0, 0, 0.03)",
        "2xs": "0 1px 2px rgba(20,33,63,.06)",
        xs: "0 1px 3px rgba(20,33,63,.08)",
      },
    },
  },
  plugins: [
    plugin(({ addBase }) => {
      addBase({ ":root": light, ".dark": { ...dark, "color-scheme": "dark" } });
    }),
  ],
};
