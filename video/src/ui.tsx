import React from "react";
import {
  AbsoluteFill, Easing, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
import { C, FONT_DISPLAY, FONT_TEXT } from "./brand";

// ---------- layout ----------
export const useLayout = () => {
  const { width: w, height: h, fps } = useVideoConfig();
  const u = Math.min(w, h) / 1080;          // 1 unit = 1px at 1080 on the short side
  const aspect = w > h * 1.2 ? "wide" : h > w * 1.2 ? "tall" : "square";
  return { w, h, u, fps, aspect } as const;
};

export const ease = Easing.bezier(0.2, 0.7, 0.2, 1);
export const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
export const fadeIO = (f: number, dur: number, inF = 8, outF = 8) =>
  Math.min(inF > 0 ? interpolate(f, [0, inF], [0, 1], clamp) : 1, outF > 0 ? interpolate(f, [dur - outF, dur], [1, 0], clamp) : 1);

// ---------- photos ----------
// Stock photos for the first cut (the script allows them). Act one is graded cold and flat.
export const Photo: React.FC<{
  src: string; dur: number; from?: number; to?: number; ox?: number; oy?: number; cold?: boolean; warm?: boolean; dim?: number;
}> = ({ src, dur, from = 1.04, to = 1.14, ox = 50, oy = 50, cold, warm, dim = 0 }) => {
  const f = useCurrentFrame();
  const s = interpolate(f, [0, dur], [from, to], clamp);
  const filter = cold ? "grayscale(.75) contrast(1.05) brightness(.72)" : warm ? "saturate(1.08) brightness(1.04)" : "none";
  return (
    <AbsoluteFill style={{ overflow: "hidden", background: "#000" }}>
      <Img src={staticFile(src)} style={{
        width: "100%", height: "100%", objectFit: "cover", objectPosition: `${ox}% ${oy}%`,
        transform: `scale(${s})`, transformOrigin: `${ox}% ${oy}%`, filter,
      }} />
      {cold && <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(20,30,48,.25), rgba(10,14,22,.55))", mixBlendMode: "multiply" }} />}
      {dim > 0 && <AbsoluteFill style={{ background: `rgba(0,0,0,${dim})` }} />}
    </AbsoluteFill>
  );
};

// ---------- the shop shutter ----------
export const Shutter: React.FC<{ startF: number; durF: number; dir?: "up" | "down" }> = ({ startF, durF, dir = "up" }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [startF, startF + durF], [0, 1], { ...clamp, easing: Easing.inOut(Easing.cubic) });
  const y = dir === "up" ? -p * 100 : (1 - p) * -100;
  return (
    <AbsoluteFill style={{
      transform: `translateY(${y}%)`,
      background: "repeating-linear-gradient(180deg, #5d636b 0px, #7d838b 10px, #4b5057 22px, #3a3e44 26px)",
      boxShadow: "0 30px 60px rgba(0,0,0,.6)",
    }}>
      <AbsoluteFill style={{ background: "linear-gradient(90deg, rgba(0,0,0,.35), rgba(255,255,255,.04) 50%, rgba(0,0,0,.35))" }} />
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: "2.5%", background: "#2a2d31", borderTop: "2px solid #8d939a" }} />
    </AbsoluteFill>
  );
};

// ---------- text chips (the script's on-screen text) ----------
export const Chip: React.FC<{
  text: string; dur: number; dark?: boolean; strong?: boolean; tone?: "red" | "green" | "blue";
  pos?: "top" | "center" | "upper";
}> = ({ text, dur, dark, strong, tone, pos = "top" }) => {
  const f = useCurrentFrame();
  const { u, aspect, fps } = useLayout();
  const sIn = spring({ frame: f, fps, config: { damping: 18, stiffness: 160 } });
  const o = fadeIO(f, dur, 6, 8);
  const dot = tone === "red" ? C.red : tone === "green" ? C.green : tone === "blue" ? C.sapphireLight : null;
  const top = pos === "center" ? "50%" : pos === "upper" ? (aspect === "tall" ? "22%" : "16%") : aspect === "tall" ? "9%" : "7%";
  return (
    <div style={{
      position: "absolute", left: "50%", top, opacity: o,
      transform: `translate(-50%, ${pos === "center" ? "-50%" : "0"}) translateY(${(1 - sIn) * 14 * u}px) scale(${0.96 + sIn * 0.04})`,
      display: "flex", alignItems: "center", gap: 14 * u, whiteSpace: "nowrap",
      padding: `${(strong ? 18 : 14) * u}px ${(strong ? 30 : 24) * u}px`, borderRadius: 999,
      background: dark ? "rgba(20,22,27,.82)" : "rgba(255,255,255,.96)",
      color: dark ? "#f3f4f6" : C.ink,
      border: dark ? "1px solid rgba(255,255,255,.12)" : `1px solid ${C.lineCool}`,
      boxShadow: dark ? "0 10px 30px rgba(0,0,0,.4)" : "0 14px 40px rgba(16,24,64,.16)",
      fontFamily: FONT_DISPLAY, fontWeight: strong ? 700 : 600, fontSize: (strong ? 40 : 32) * u, letterSpacing: -0.3 * u,
    }}>
      {dot && <span style={{ width: 14 * u, height: 14 * u, borderRadius: 99, background: dot, boxShadow: `0 0 0 ${5 * u}px ${dot}33` }} />}
      {text}
    </div>
  );
};

// ---------- Tekly mark ----------
const MARK_PATH = "M59.50 27.67Q62.00 32.00 59.50 36.33L49.50 53.65Q47.00 57.98 42.00 57.98L22.00 57.98Q17.00 57.98 14.50 53.65L4.50 36.33Q2.00 32.00 4.50 27.67L14.50 10.35Q17.00 6.02 22.00 6.02L42.00 6.02Q47.00 6.02 49.50 10.35ZM24.5 19.5L34.5 15V25.5H42V33H34.5V40C34.5 41.8 35.4 42.5 37.2 42.5H42V49.8H35.8C28.6 49.8 24.5 46 24.5 39.5V33H20.5V25.5H24.5Z";
export const TeklyMark: React.FC<{ size: number; light?: boolean }> = ({ size, light }) => {
  const id = light ? "tkl" : "tkd";
  return (
    <svg width={size} height={size} viewBox="0 0 64 64">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          {light ? <><stop offset="0" stopColor="#7cb2ff" /><stop offset=".55" stopColor="#2a6ff0" /><stop offset="1" stopColor="#1240b8" /></>
            : <><stop offset="0" stopColor="#2c72d9" /><stop offset="1" stopColor="#0a2f8f" /></>}
        </linearGradient>
      </defs>
      <path fillRule="evenodd" fill={`url(#${id})`} d={MARK_PATH} />
    </svg>
  );
};

export const Wordmark: React.FC<{ size: number; color?: string; light?: boolean }> = ({ size, color = C.ink, light }) => (
  <div style={{ display: "flex", alignItems: "center", gap: size * 0.28 }}>
    <TeklyMark size={size * 1.1} light={light} />
    {/* the full product name: teklyGO */}
    <span style={{ fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: size, letterSpacing: -size * 0.04, color }}>
      tekly<span style={{ color: light ? "#7cb2ff" : C.sapphire, letterSpacing: -size * 0.02 }}>GO</span>
    </span>
  </div>
);

// ---------- trade icons: phone, car, chainsaw, outboard ----------
const stroke = (c: string, w: number) => ({ fill: "none", stroke: c, strokeWidth: w, strokeLinecap: "round" as const, strokeLinejoin: "round" as const });
export const TradeIcon: React.FC<{ kind: "phone" | "car" | "chainsaw" | "outboard"; size: number; color?: string }> = ({ kind, size, color = C.sapphire }) => {
  const s = stroke(color, 3.2);
  return (
    <svg width={size} height={size} viewBox="0 0 64 64">
      {kind === "phone" && <><rect x="19" y="6" width="26" height="52" rx="5" {...s} /><path d="M28 12h8M30 51h4" {...s} /></>}
      {kind === "car" && <><path d="M8 40v-7l6-11c1-2 3-3 5-3h26c2 0 4 1 5 3l6 11v7c0 2-1 3-3 3H11c-2 0-3-1-3-3z" {...s} /><path d="M12 32h40M18 22l-3 10M46 22l3 10" {...s} /><circle cx="19" cy="44" r="5" {...s} /><circle cx="45" cy="44" r="5" {...s} /></>}
      {kind === "chainsaw" && <><path d="M6 30h30v12H6z" {...s} /><path d="M36 33h18c3 0 5 1.5 5 3s-2 3-5 3H36" {...s} /><path d="M12 30v-8c0-2 2-4 4-4h10c2 0 4 2 4 4v8" {...s} /><path d="M10 42v6h16v-6" {...s} /><path d="M40 33v6M45 33v6M50 33v6" {...s} /></>}
      {kind === "outboard" && <><path d="M22 6h18c3 0 5 2 5 5v9c0 3-2 5-5 5H22c-3 0-5-2-5-5v-9c0-3 2-5 5-5z" {...s} /><path d="M27 25v24M35 25v24" {...s} /><path d="M24 49h16l-2 6H26z" {...s} /><path d="M40 52h8M44 47v10" {...s} /></>}
    </svg>
  );
};

// ---------- frames for the real app recordings ----------
export type Zoom = { t: number; s: number; x: number; y: number }; // t in seconds of the shot, x/y = focus point 0..1 in the clip

const zoomAt = (keys: Zoom[], sec: number) => {
  if (!keys.length) return { s: 1, x: 0.5, y: 0.5 };
  if (sec <= keys[0].t) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (sec <= b.t) {
      const p = Easing.inOut(Easing.cubic)((sec - a.t) / Math.max(0.001, b.t - a.t));
      return { s: a.s + (b.s - a.s) * p, x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p };
    }
  }
  return keys[keys.length - 1];
};

// Plays a clip inside a fixed box, zooming toward focus points. The clip is scaled to the box,
// then magnified around (x, y) without ever showing past its edges.
export const ClipView: React.FC<{
  src: string; startFrom?: number; rate?: number; zoom?: Zoom[]; boxW: number; boxH: number; clipW: number; clipH: number; clipDur?: number;
}> = ({ src, startFrom = 0, rate = 1, zoom = [], boxW, boxH, clipW, clipH, clipDur = Infinity }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const z = zoomAt(zoom, f / fps);
  const base = Math.max(boxW / clipW, boxH / clipH);
  const vw = clipW * base, vh = clipH * base;
  const s = Math.max(1, z.s);
  // translate so the focus point is centred, clamped to edges
  const cx = z.x * vw * s, cy = z.y * vh * s;
  const tx = Math.min(0, Math.max(boxW - vw * s, boxW / 2 - cx));
  const ty = Math.min(0, Math.max(boxH - vh * s, boxH / 2 - cy));
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#fff" }}>
      <div style={{ position: "absolute", left: 0, top: 0, width: vw, height: vh, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${s})` }}>
        {startFrom + (f / fps) * rate < clipDur - 0.15
          ? <OffthreadVideo src={staticFile(src)} startFrom={Math.round(startFrom * fps)} playbackRate={rate} muted
              style={{ width: vw, height: vh, display: "block" }} />
          : <Img src={staticFile(src.replace(/\.webm$/, "-last.jpg"))} style={{ width: vw, height: vh, display: "block" }} />}
      </div>
    </div>
  );
};

export const BrowserFrame: React.FC<{ w: number; h: number; children: React.ReactNode; url?: string }> = ({ w, h, children, url = "app.tekly.app" }) => {
  const { u } = useLayout();
  const bar = 44 * u;
  return (
    <div style={{
      width: w, height: h + bar, borderRadius: 14 * u, overflow: "hidden", background: "#fff",
      boxShadow: "0 40px 90px rgba(16,24,64,.22), 0 0 0 1px rgba(16,24,64,.08)", position: "relative",
    }}>
      <div style={{ height: bar, background: "#f3f4f7", borderBottom: `1px solid ${C.line}`, display: "flex", alignItems: "center", gap: 8 * u, padding: `0 ${16 * u}px` }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => <span key={c} style={{ width: 12 * u, height: 12 * u, borderRadius: 99, background: c }} />)}
        <div style={{ marginLeft: 16 * u, flex: 1, maxWidth: 520 * u, height: 26 * u, borderRadius: 8 * u, background: "#fff", border: `1px solid ${C.line}`,
          fontFamily: FONT_TEXT, fontSize: 14 * u, color: C.inkMuted, display: "flex", alignItems: "center", paddingLeft: 12 * u }}>{url}</div>
      </div>
      <div style={{ position: "relative", width: w, height: h }}>{children}</div>
    </div>
  );
};

export const PhoneFrame: React.FC<{ h: number; children: React.ReactNode }> = ({ h, children }) => {
  const w = h * (390 / 844);
  const bez = h * 0.022;
  return (
    <div style={{
      width: w + bez * 2, height: h + bez * 2, borderRadius: h * 0.085, background: "#111318", padding: bez,
      boxShadow: "0 50px 100px rgba(16,24,64,.3), inset 0 0 0 2px #2a2d33", position: "relative",
    }}>
      <div style={{ width: w, height: h, borderRadius: h * 0.07, overflow: "hidden", position: "relative", background: "#fff" }}>
        {children}
        <div style={{ position: "absolute", top: h * 0.012, left: "50%", transform: "translateX(-50%)", width: w * 0.3, height: h * 0.034, borderRadius: 99, background: "#0b0c0f" }} />
      </div>
    </div>
  );
};

// ---------- bright act-two stage ----------
export const Stage: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill style={{ background: `radial-gradient(120% 90% at 50% 0%, #ffffff 0%, ${C.surface} 60%, #e9eef8 100%)` }}>{children}</AbsoluteFill>
);

export const Centered: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", ...style }}>{children}</AbsoluteFill>
);
