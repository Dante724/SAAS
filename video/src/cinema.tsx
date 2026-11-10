// The film layer: grain, vignette, letterbox, handheld drift, 3D device tilt, whip transitions,
// light leaks and the opening rewind. Everything is deterministic per frame (no Math.random).
import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig, Easing } from "remotion";
import { clamp, useLayout } from "./ui";

// smooth pseudo-noise for camera drift
const n1 = (t: number, seed: number) => Math.sin(t * 1.3 + seed) * 0.6 + Math.sin(t * 2.7 + seed * 2.1) * 0.3 + Math.sin(t * 5.1 + seed * 3.7) * 0.1;

export const Grain: React.FC<{ amount?: number }> = ({ amount = 0.07 }) => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: "none", mixBlendMode: "overlay", opacity: amount }}>
      <svg width="100%" height="100%">
        <filter id={`g${f % 8}`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed={f % 8} stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter={`url(#g${f % 8})`} />
      </svg>
    </AbsoluteFill>
  );
};

export const Vignette: React.FC<{ strength?: number }> = ({ strength = 0.45 }) => (
  <AbsoluteFill style={{ pointerEvents: "none", background: `radial-gradient(120% 95% at 50% 50%, rgba(0,0,0,0) 55%, rgba(0,0,0,${strength}) 100%)` }} />
);

// 2.39:1 bars on wide frames during act one; they open up at the turn.
export const Letterbox: React.FC<{ until: number; from?: number; openFrames?: number }> = ({ until, from = 0, openFrames = 18 }) => {
  const f = useCurrentFrame();
  const { w, h, aspect } = useLayout();
  if (aspect !== "wide") return null;
  const bar = Math.max(0, (h - w / 2.39) / 2);
  const p = Math.min(
    from > 0 ? interpolate(f, [from, from + 10], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) }) : 1,
    interpolate(f, [until, until + openFrames], [1, 0], { ...clamp, easing: Easing.inOut(Easing.cubic) }));
  if (p <= 0) return null;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={{ position: "absolute", left: 0, right: 0, top: 0, height: bar * p, background: "#000" }} />
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: bar * p, background: "#000" }} />
    </AbsoluteFill>
  );
};

// Slow handheld drift + breathing scale, for live-action (photo) shots.
export const Handheld: React.FC<{ children: React.ReactNode; amp?: number; seed?: number }> = ({ children, amp = 1, seed = 1 }) => {
  const f = useCurrentFrame();
  const { fps, u } = useLayout();
  const t = f / fps;
  const x = n1(t * 0.6, seed) * 9 * u * amp, y = n1(t * 0.5, seed + 4) * 6 * u * amp, r = n1(t * 0.4, seed + 9) * 0.25 * amp;
  return <AbsoluteFill style={{ transform: `translate(${x}px, ${y}px) rotate(${r}deg) scale(${1.03 + 0.01 * amp})` }}>{children}</AbsoluteFill>;
};

// A device floating in space: gentle perspective tilt that eases flat, with a soft floor shadow.
export const Float3D: React.FC<{ children: React.ReactNode; dur: number; from?: number; to?: number; tiltX?: number; seed?: number }> =
  ({ children, dur, from = -10, to = -3, tiltX = 6, seed = 2 }) => {
    const f = useCurrentFrame();
    const { fps } = useLayout();
    const p = interpolate(f, [0, dur], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
    const ry = from + (to - from) * p + n1(f / fps * 0.3, seed) * 0.6;
    const rx = tiltX * (1 - p) + 1.5 + n1(f / fps * 0.25, seed + 3) * 0.4;
    return (
      <div style={{ perspective: 2400, perspectiveOrigin: "50% 40%" }}>
        <div style={{ transform: `rotateY(${ry}deg) rotateX(${rx}deg)`, transformStyle: "preserve-3d" }}>{children}</div>
      </div>
    );
  };

// Act two backdrop: the shop itself, blurred and brightened, so devices sit in a real place.
export const ShopBackdrop: React.FC<{ src?: string; dur: number; tint?: number }> = ({ src = "photos/workshop-garage-960.webp", dur, tint = 0.8 }) => {
  const f = useCurrentFrame();
  const s = interpolate(f, [0, dur], [1.12, 1.18], clamp);
  return (
    <AbsoluteFill style={{ overflow: "hidden", background: "#eef2f9" }}>
      <Img src={staticFile(src)} style={{ width: "100%", height: "100%", objectFit: "cover", filter: "blur(28px) saturate(1.1)", transform: `scale(${s})` }} />
      <AbsoluteFill style={{ background: `linear-gradient(180deg, rgba(246,248,252,${tint}) 0%, rgba(236,241,250,${tint - 0.06}) 60%, rgba(226,233,246,${tint}) 100%)` }} />
      <AbsoluteFill style={{ background: "radial-gradient(70% 55% at 50% 45%, rgba(255,255,255,.55), rgba(255,255,255,0) 70%)" }} />
    </AbsoluteFill>
  );
};

// Whip transition: the scene arrives with a fast horizontal slide + motion blur, and leaves the same way.
export const Whip: React.FC<{ children: React.ReactNode; dur: number; inF?: number; outF?: number; dir?: 1 | -1 }> = ({ children, dur, inF = 9, outF = 7, dir = 1 }) => {
  const f = useCurrentFrame();
  const { width: w } = useVideoConfig();
  const a = inF ? interpolate(f, [0, inF], [1, 0], { ...clamp, easing: Easing.out(Easing.cubic) }) : 0;
  const b = outF ? interpolate(f, [dur - outF, dur], [0, 1], { ...clamp, easing: Easing.in(Easing.cubic) }) : 0;
  const x = (a * 0.35 - b * 0.35) * w * dir;
  const blur = (a + b) * 38;
  return <AbsoluteFill style={{ transform: `translateX(${x}px)`, filter: blur > 0.5 ? `blur(${blur}px)` : undefined }}>{children}</AbsoluteFill>;
};

// A warm light leak that sweeps once (used at the turn).
export const LightLeak: React.FC<{ at: number; len?: number }> = ({ at, len = 40 }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [at, at + len], [0, 1], clamp);
  const o = Math.sin(Math.PI * p) * 0.55;
  if (o <= 0.001) return null;
  return (
    <AbsoluteFill style={{ pointerEvents: "none", mixBlendMode: "screen", opacity: o,
      background: `radial-gradient(60% 80% at ${-10 + 120 * p}% 30%, rgba(255,190,120,.9), rgba(255,120,60,.25) 40%, rgba(0,0,0,0) 70%)` }} />
  );
};

// White flash (camera flash / cut accent)
export const Flash: React.FC<{ at: number; len?: number; peak?: number }> = ({ at, len = 8, peak = 0.9 }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [at, at + 1, at + len], [0, peak, 0], clamp);
  return o > 0 ? <AbsoluteFill style={{ background: "#fff", opacity: o, pointerEvents: "none" }} /> : null;
};

// The rewind: frame jitters, RGB split and scanlines while time runs backwards.
export const RewindFX: React.FC<{ children: React.ReactNode; start: number; len: number }> = ({ children, start, len }) => {
  const f = useCurrentFrame();
  const { u } = useLayout();
  const p = interpolate(f, [start, start + len], [0, 1], clamp);
  const on = f >= start && f < start + len;
  const k = on ? Math.sin(Math.PI * p) : 0;
  const jx = on ? ((f * 37) % 11 - 5) * 3 * u * k : 0;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ transform: `translateX(${jx}px) scale(${1 + 0.06 * k})`, filter: k > 0 ? `blur(${k * 3}px) contrast(${1 + 0.3 * k})` : undefined }}>{children}</AbsoluteFill>
      {k > 0 && <>
        <AbsoluteFill style={{ background: `rgba(255,0,60,${0.12 * k})`, mixBlendMode: "screen", transform: `translateX(${12 * u * k}px)` }} />
        <AbsoluteFill style={{ background: `rgba(0,180,255,${0.12 * k})`, mixBlendMode: "screen", transform: `translateX(${-12 * u * k}px)` }} />
        <AbsoluteFill style={{ opacity: 0.35 * k, background: "repeating-linear-gradient(0deg, rgba(255,255,255,.25) 0 2px, rgba(0,0,0,0) 2px 6px)", transform: `translateY(${(f * 9) % 6}px)` }} />
      </>}
    </AbsoluteFill>
  );
};
