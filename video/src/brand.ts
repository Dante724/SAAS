// Brand tokens. Sapphire mark colours from the Tekly logo (frontend/static/icons/tekly-mark.svg),
// ink, canvas and status colours from website/styles.css and DESIGN.md. Type: Sora display, Inter body.
import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

export const C = {
  sapphire: "#0059ba",
  sapphireLight: "#2c72d9",
  sapphireDeep: "#0a2f8f",
  ink: "#23263b",
  inkSoft: "#36395a",
  inkMuted: "#484c7a",
  canvas: "#ffffff",
  surface: "#f4f6fb",
  line: "#e5e7eb",
  lineCool: "#d6d6e7",
  caption: "#9698c3",
  deep: "#000033",
  green: "#2bb673",
  amber: "#f5a623",
  red: "#f2645a",
  sticky: "#ffe27a",
  // act one: cold, flat greys of "the old software"
  grey0: "#1b1d22",
  grey1: "#2a2d33",
  grey2: "#d9dadd",
  grey3: "#c3c5ca",
  grey4: "#a3a6ad",
  grey5: "#eceded",
};

export const FONT_DISPLAY = "Sora, Arial, sans-serif";
export const FONT_TEXT = "Inter, system-ui, sans-serif";

let loaded: Promise<unknown> | null = null;
export const loadBrandFonts = () => {
  if (!loaded) {
    loaded = Promise.all([
      loadFont({ family: "Sora", url: staticFile("fonts/sora-latin-wght-normal.woff2"), weight: "100 900" }),
      loadFont({ family: "Inter", url: staticFile("fonts/inter-latin-wght-normal.woff2"), weight: "100 900" }),
    ]);
  }
  return loaded;
};
