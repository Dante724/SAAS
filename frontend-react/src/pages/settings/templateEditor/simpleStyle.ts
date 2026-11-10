// The Design view's simple style panel: a handful of choices in plain words, each written as
// ordinary CSS on the selected element. Everything else stays under "More".
export type Style = Record<string, string>;

export const FONTS = ["Inter", "Arial", "Helvetica", "Georgia", "Times New Roman", "Courier New", "Verdana", "Tahoma", "Roboto", "Trebuchet MS"];
export const SIZES = [["small", "Small", "11px"], ["normal", "Normal", "13px"], ["large", "Large", "16px"], ["title", "Title", "22px"]] as const;
export const SPACING = [["tight", "Tight", "0 0 2px"], ["normal", "Normal", "0 0 8px"], ["roomy", "Roomy", "0 0 16px"]] as const;
export const ALIGN = ["left", "center", "right"] as const;
export const COLOURS = ["#111111", "#555555", "#0059ba", "#b91c1c", "#15803d"];

export type Simple = { font: string | null; size: string | null; bold: boolean; colour: string | null; align: string | null; spacing: string | null };

/** The simple choices an element's style currently matches (null = not set, or a custom value). */
export function readSimple(s: Style): Simple {
  const font = (s["font-family"] || "").split(",")[0].replace(/["']/g, "").trim() || null;
  return {
    font,
    size: SIZES.find(([, , px]) => px === s["font-size"])?.[0] ?? null,
    bold: /^(bold|[6-9]00)$/.test(s["font-weight"] || ""),
    colour: s.color || null,
    align: s["text-align"] || null,
    spacing: SPACING.find(([, , m]) => m === s.margin)?.[0] ?? null,
  };
}

/** The element's style with one simple choice applied. */
export function applySimple(s: Style, key: keyof Simple, value: string | boolean | null): Style {
  const out = { ...s };
  const set = (k: string, v: string | null) => { if (v == null || v === "") delete out[k]; else out[k] = v; };
  switch (key) {
    case "font": set("font-family", value ? `${value}, Arial, sans-serif` : null); break;
    case "size": set("font-size", SIZES.find(([k]) => k === value)?.[2] ?? null); break;
    case "bold": set("font-weight", value ? "bold" : null); break;
    case "colour": set("color", (value as string) || null); break;
    case "align": set("text-align", (value as string) || null); break;
    case "spacing":
      // margin replaces any longhand margins so the choice always shows
      for (const k of ["margin-top", "margin-right", "margin-bottom", "margin-left"]) delete out[k];
      set("margin", SPACING.find(([k]) => k === value)?.[2] ?? null); break;
  }
  return out;
}
