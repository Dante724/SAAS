import type { ReactNode } from "react";
import { hueText, tint } from "../../lib/tint";

/* One pill for "is this real money / real calls?" across Settings › Integrations
   (Calls & telephony, Card readers). Same soft-tint look as billing's StatusPill:
   amber for test, green for live, slate for off. The words are "Test mode" and "Live". */
export type Mode = "test" | "live" | "off";
const HUE: Record<Mode, string> = { test: "#BA7517", live: "#1D9E75", off: "#64748b" };
const WORD: Record<Mode, string> = { test: "Test mode", live: "Live", off: "Off" };

export default function ModePill({ mode, children, testId }: { mode: Mode; children?: ReactNode; testId?: string }) {
  const hue = HUE[mode];
  return (
    <span data-testid={testId} className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>
      {children ?? WORD[mode]}
    </span>
  );
}
