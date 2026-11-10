import React from "react";
import { AbsoluteFill, Sequence, interpolate, spring, useCurrentFrame } from "remotion";
import { C, FONT_DISPLAY, FONT_TEXT } from "./brand";
import { captionCues, type Cut } from "./captions";
import { clamp, useLayout } from "./ui";

// Captions on every version, driven only by captions.ts.
export const Captions: React.FC<{ cut: Cut }> = ({ cut }) => {
  const { fps } = useLayout();
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {captionCues(cut).map((c, i) => {
        const from = Math.round(c.from * fps);
        const len = Math.round(c.to * fps) - from;
        return (
          <Sequence key={i} from={from} durationInFrames={len} layout="none">
            {c.kind === "bold" ? <Bold text={c.text} dur={len} /> : <Line text={c.text} dur={len} />}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

const Line: React.FC<{ text: string; dur: number }> = ({ text, dur }) => {
  const f = useCurrentFrame();
  const { u, aspect } = useLayout();
  const o = Math.min(interpolate(f, [0, 4], [0, 1], clamp), interpolate(f, [dur - 3, dur], [1, 0], clamp));
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: aspect === "tall" ? "13%" : "6.5%", display: "flex", justifyContent: "center", opacity: o }}>
      <div style={{
        maxWidth: aspect === "wide" ? 1300 * u : 920 * u, textAlign: "center", padding: `${12 * u}px ${24 * u}px`, borderRadius: 14 * u,
        background: "rgba(14,16,24,.74)", color: "#fff", fontFamily: FONT_TEXT, fontWeight: 600, fontSize: (aspect === "wide" ? 38 : 42) * u,
        lineHeight: 1.3, letterSpacing: -0.2 * u, textWrap: "balance",
      } as React.CSSProperties}>{text}</div>
    </div>
  );
};

// The 15-second cut has no voice: each caption is the message, set big.
const Bold: React.FC<{ text: string; dur: number }> = ({ text, dur }) => {
  const f = useCurrentFrame();
  const { u, fps, aspect } = useLayout();
  const s = spring({ frame: f, fps, config: { damping: 18, stiffness: 160 } });
  const o = Math.min(s, interpolate(f, [dur - 4, dur], [1, 0], clamp));
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: aspect === "tall" ? "11%" : "7%", display: "flex", justifyContent: "center", opacity: o,
      transform: `translateY(${(1 - s) * 20 * u}px)` }}>
      <div style={{
        maxWidth: (aspect === "wide" ? 1500 : 940) * u, textAlign: "center", padding: `${20 * u}px ${34 * u}px`, borderRadius: 22 * u,
        background: C.ink, color: "#fff", fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: (aspect === "tall" ? 66 : 58) * u,
        lineHeight: 1.12, letterSpacing: -1.2 * u, boxShadow: "0 20px 50px rgba(0,0,0,.3)", textWrap: "balance",
      } as React.CSSProperties}>{text}</div>
    </div>
  );
};
