import React from "react";
import { AbsoluteFill, Sequence, spring, useCurrentFrame } from "remotion";
import { TradeFan } from "./Montage";
import { C, FONT_DISPLAY, FONT_TEXT } from "../brand";
import { Stage, TradeIcon, Wordmark, useLayout } from "../ui";

// Logo, four trade icons (phone, car, chainsaw, outboard), tekly.app. No swell, no fireworks.
// `fan`: frames of the opening where four kinds of shop show their own check-in page, then the logo.
export const EndCard: React.FC<{ dur: number; line?: string; sub?: string; showTrades?: boolean; fan?: number }> = ({ dur, line, sub, showTrades = true, fan = 0 }) => {
  if (fan > 0) return (
    <AbsoluteFill>
      <Sequence durationInFrames={fan}><TradeFan dur={fan} /></Sequence>
      <Sequence from={fan - 6}><EndCard dur={dur - fan + 6} line={line} sub={sub} showTrades={showTrades} /></Sequence>
    </AbsoluteFill>
  );
  return <Logo line={line} sub={sub} showTrades={showTrades} />;
};

const Logo: React.FC<{ line?: string; sub?: string; showTrades?: boolean }> = ({ line, sub, showTrades = true }) => {
  const f = useCurrentFrame();
  const { u, fps } = useLayout();
  const a = spring({ frame: f, fps, config: { damping: 20, stiffness: 110 } });
  const b = spring({ frame: f - 10, fps, config: { damping: 20, stiffness: 110 } });
  const c = spring({ frame: f - 20, fps, config: { damping: 20, stiffness: 110 } });
  return (
    <Stage>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 34 * u }}>
        <div style={{ opacity: a, transform: `translateY(${(1 - a) * 18 * u}px)` }}><Wordmark size={118 * u} /></div>
        {line && <div style={{ opacity: b, fontFamily: FONT_DISPLAY, fontWeight: 600, fontSize: 40 * u, color: C.inkSoft, letterSpacing: -0.6 * u, textAlign: "center", padding: `0 ${60 * u}px` }}>{line}</div>}
        {showTrades && (
          <div style={{ opacity: b, display: "flex", gap: 34 * u, marginTop: 6 * u }}>
            {(["phone", "car", "chainsaw", "outboard"] as const).map((k) => (
              <div key={k} style={{ width: 104 * u, height: 104 * u, borderRadius: 26 * u, background: "#fff", border: `1px solid ${C.lineCool}`,
                display: "grid", placeItems: "center", boxShadow: "0 10px 30px rgba(16,24,64,.08)" }}>
                <TradeIcon kind={k} size={60 * u} />
              </div>
            ))}
          </div>
        )}
        <div style={{ opacity: c, marginTop: 10 * u, fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: 52 * u, color: C.sapphire, letterSpacing: -1 * u }}>tekly.app</div>
        {sub && <div style={{ opacity: c, fontFamily: FONT_TEXT, fontWeight: 500, fontSize: 30 * u, color: C.inkMuted }}>{sub}</div>}
      </AbsoluteFill>
    </Stage>
  );
};
