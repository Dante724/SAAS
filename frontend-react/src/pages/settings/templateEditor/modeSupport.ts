// Which views can safely open a template. A view is offered only when loading the template into
// it and saving straight back gives the same document with every placeholder in the same order;
// otherwise its tab is greyed out with a one-line reason (owner rule: grey out, don't hide).
// The libraries load on demand, so this file stays small.
import { LOST_IN, placeholders, sameTemplate, staticReasons, type ModeId } from "./templateHtml";

export const loadText = () => import("./tiptapSchema");
export const loadDesign = () => import("./grapesSetup");

export async function roundTrip(mode: "design" | "text", html: string): Promise<string> {
  return mode === "text" ? (await loadText()).textRoundTrip(html) : (await loadDesign()).designRoundTrip(html);
}

/** Null when `mode` can open and save this template unchanged, else the reason it can't. */
export async function modeReason(mode: ModeId, html: string): Promise<string | null> {
  const fixed = staticReasons(html)[mode];
  if (fixed || mode === "html") return fixed;
  try {
    const out = await roundTrip(mode, html);
    if (!sameTemplate(html, out) || placeholders(html).join("\n") !== placeholders(out).join("\n")) return LOST_IN[mode];
    return null;
  } catch {
    return mode === "design" ? "Design couldn't open this template. Edit it in HTML." : "The text view couldn't open this template. Edit it in HTML.";
  }
}

export async function allReasons(html: string): Promise<Record<ModeId, string | null>> {
  const [design, text] = await Promise.all([modeReason("design", html), modeReason("text", html)]);
  return { design, text, html: null };
}
