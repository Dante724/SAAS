// Act two: the same Tuesday in Tekly. Every screen here is a real recording of the app
// (video/capture/record.ts, seeded demo shop), framed and zoomed toward the moment that matters.
import React from "react";
import { AbsoluteFill, Sequence, interpolate, spring, useCurrentFrame } from "remotion";
import manifest from "../../public/clips/manifest.json";
import { SHOTS, type Shot } from "../shots";
import { Float3D } from "../cinema";
import { BrowserFrame, ClipView, PhoneFrame, Photo, Stage, clamp, useLayout } from "../ui";
import { FONT_DISPLAY } from "../brand";

type P = { dur: number };
type ClipInfo = { file: string; width: number; height: number; durationSec: number };
export const hasClip = (name: string) => !!(manifest as unknown as { clips: Record<string, ClipInfo> }).clips[name];
export const info = (name: string): ClipInfo => {
  const c = (manifest as unknown as { clips: Record<string, ClipInfo> }).clips[name];
  if (!c) throw new Error(`clip ${name} missing from public/clips/manifest.json — run npm run capture`);
  return c;
};

// The desktop app in a browser window. On square/vertical the box is narrower, so the zoom works harder.
export const DesktopShot: React.FC<{ shot: Shot; dur: number; enter?: boolean; scale?: number; dx?: number; dy?: number }> = ({ shot, dur, enter = true, scale = 1, dx = 0, dy = 0 }) => {
  const f = useCurrentFrame();
  const { u, aspect, fps } = useLayout();
  const c = info(shot.clip);
  const box = aspect === "wide" ? { w: 1260 * u, h: 788 * u } : aspect === "square" ? { w: 900 * u, h: 720 * u } : { w: 980 * u, h: 1180 * u };   // inside a 6% safe margin, clear of captions
  const extra = 1;   // on square/vertical the cover-fit crop already brings the camera closer
  const zoom = (shot.zoom ?? [{ t: 0, s: 1, x: 0.5, y: 0.5 }]).map((z) => ({ ...z, s: z.s * extra }));
  const s = enter ? spring({ frame: f, fps, config: { damping: 20, stiffness: 120 } }) : 1;
  const out = interpolate(f, [dur - 6, dur], [1, 0.0], clamp);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", paddingBottom: (aspect === "tall" ? 120 : 90) * u }}>
      <div style={{ transform: `translate(${dx * u}px, ${dy * u + (1 - s) * 40 * u}px) scale(${(0.97 + 0.03 * s) * scale})`, opacity: Math.min(s, shot.fadeOut ? out : 1) }}>
        <Float3D dur={dur} from={-7} to={-1.5} tiltX={4}>
          <BrowserFrame w={box.w} h={box.h}>
            <ClipView src={`clips/${c.file}`} clipW={c.width} clipH={c.height} boxW={box.w} boxH={box.h} startFrom={shot.from} rate={shot.rate} clipDur={c.durationSec} zoom={zoom} />
          </BrowserFrame>
        </Float3D>
      </div>
    </AbsoluteFill>
  );
};

export const MobileShot: React.FC<{ shot: Shot; dur: number; x?: string; y?: string; height?: number; delay?: number }> = ({ shot, dur, x = "50%", y = "50%", height, delay = 0 }) => {
  const f = useCurrentFrame();
  const { u, aspect, fps } = useLayout();
  const c = info(shot.clip);
  const h = (height ?? (aspect === "tall" ? 1120 : 760)) * u;
  const w = h * (390 / 844);
  const s = spring({ frame: f - delay, fps, config: { damping: 20, stiffness: 120 } });
  return (
    <div style={{ position: "absolute", left: x, top: y, transform: `translate(-50%, -50%) translateY(${(1 - s) * 60 * u - (aspect === "tall" ? 40 : 18) * u}px)`, opacity: s }}>
      <Float3D dur={dur} from={12} to={4} tiltX={5} seed={6}>
        <PhoneFrame h={h}>
          <ClipView src={`clips/${c.file}`} clipW={c.width} clipH={c.height} boxW={w} boxH={h} startFrom={shot.from} rate={shot.rate} clipDur={c.durationSec} zoom={shot.zoom} />
        </PhoneFrame>
      </Float3D>
    </div>
  );
};

const seconds = (fps: number, s: number) => Math.round(s * fps);

// 0:42 intake: photo, circle the crack, the job exists
export const Intake: React.FC<P> = ({ dur }) => <Stage><DesktopShot shot={SHOTS.intake} dur={dur} /></Stage>;

// 0:52 the approval: Maya's phone, then the board flips to Approved
export const Approval: React.FC<P> = ({ dur }) => {
  const { fps, aspect } = useLayout();
  const switchAt = seconds(fps, SHOTS.approvalSwitchAt);
  return (
    <Stage>
      <Sequence durationInFrames={aspect === "wide" ? dur : switchAt + 8} layout="none">
        <MobileShotSlide dur={dur} switchAt={switchAt} />
      </Sequence>
      <Sequence from={switchAt} layout="none">
        <BoardBeside dur={dur - switchAt} />
      </Sequence>
    </Stage>
  );
};

// on wide screens the phone slides left and the board comes in beside it; elsewhere the board replaces it
const MobileShotSlide: React.FC<{ dur: number; switchAt: number }> = ({ dur, switchAt }) => {
  const f = useCurrentFrame();
  const { aspect, fps } = useLayout();
  const p = spring({ frame: f - switchAt, fps, config: { damping: 22, stiffness: 110 } });
  const x = aspect === "wide" ? `${50 - 32 * p}%` : "50%";
  const o = aspect === "wide" ? 1 : interpolate(f, [switchAt, switchAt + 8], [1, 0], clamp);
  return <AbsoluteFill style={{ opacity: o }}><MobileShot shot={SHOTS.approve} dur={dur} x={x} /></AbsoluteFill>;
};

const BoardBeside: React.FC<{ dur: number }> = ({ dur }) => {
  const { aspect, u } = useLayout();
  if (aspect !== "wide") return <DesktopShot shot={SHOTS.boardApproved} dur={dur} />;
  return (
    <AbsoluteFill style={{ left: 720 * u }}>
      <div style={{ transform: "scale(.72)", transformOrigin: "50% 50%", width: "100%", height: "100%" }}>
        <DesktopShot shot={SHOTS.boardApproved} dur={dur} />
      </div>
    </AbsoluteFill>
  );
};

// 1:04 the part: 1 in stock, reserved for this job; the clock starts
export const Part: React.FC<P> = ({ dur }) => <Stage><DesktopShot shot={SHOTS.stock} dur={dur} /></Stage>;

// 1:12 close of job: done, invoice, pay link
// 1:30 close of job: done, invoice and pay link, then the board: her job Done and paid
export const Close: React.FC<P> = ({ dur }) => {
  const { fps } = useLayout();
  const a = seconds(fps, 3.0);
  return (
    <Stage>
      <Sequence durationInFrames={a}><Stage><DesktopShot shot={SHOTS.invoice} dur={a} /></Stage></Sequence>
      <Sequence from={a}><Stage><DesktopShot shot={SHOTS.eodPaid} dur={dur - a} /></Stage></Sequence>
    </Stage>
  );
};

// 1:38 Dani's line, set as the film's title card over the shop at the end of the day
export const DanisLine: React.FC<P> = ({ dur }) => {
  const f = useCurrentFrame();
  const { u, fps, aspect } = useLayout();
  const l1 = spring({ frame: f - 6, fps, config: { damping: 22, stiffness: 90 } });
  const l2 = spring({ frame: f - Math.round(fps * 1.7), fps, config: { damping: 22, stiffness: 90 } });
  const size = (aspect === "wide" ? 76 : aspect === "square" ? 66 : 70) * u;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ filter: "blur(5px)" }}><Photo src="photos/repair-lab-2-960.webp" dur={dur} warm from={1.06} to={1.14} ox={30} oy={45} /></AbsoluteFill>
      <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(10,12,20,.35), rgba(10,12,20,.62))" }} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 22 * u, padding: `0 ${80 * u}px`, textAlign: "center",
        opacity: interpolate(f, [dur - 8, dur], [1, 0], clamp) }}>
        <div style={{ fontFamily: FONT_DISPLAY, fontWeight: 600, fontSize: size, letterSpacing: -size * 0.03, lineHeight: 1.1, color: "rgba(255,255,255,.82)",
          opacity: l1, transform: `translateY(${(1 - l1) * 24 * u}px)` }}>I didn't need more software.</div>
        <div style={{ fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: size * 1.08, letterSpacing: -size * 0.03, lineHeight: 1.1, color: "#fff",
          opacity: l2, transform: `translateY(${(1 - l2) * 24 * u}px)` }}>I needed the day back.</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---- 30-second cut ----
export const Approval30: React.FC<P> = ({ dur }) => {
  const { fps } = useLayout();
  const a = seconds(fps, 5.2), b = seconds(fps, 7.6);
  return (
    <Stage>
      <Sequence durationInFrames={a}><Stage><MobileShot shot={SHOTS.approve30} dur={a} /></Stage></Sequence>
      <Sequence from={a} durationInFrames={b - a}><Stage><DesktopShot shot={SHOTS.boardApproved} dur={b - a} /></Stage></Sequence>
      <Sequence from={b}><Stage><DesktopShot shot={SHOTS.stock30} dur={dur - b} /></Stage></Sequence>
    </Stage>
  );
};
export const Close30: React.FC<P> = ({ dur }) => <Stage><DesktopShot shot={SHOTS.invoice30} dur={dur} /></Stage>;

// ---- 15-second cut ----
export const Text15: React.FC<P> = ({ dur }) => <Stage><MobileShot shot={SHOTS.approve15} dur={dur} /></Stage>;
export const Approved15: React.FC<P> = ({ dur }) => {
  const { fps } = useLayout();
  const a = seconds(fps, 1.6);
  return (
    <Stage>
      <Sequence durationInFrames={a}><Stage><DesktopShot shot={SHOTS.boardApproved15} dur={a} /></Stage></Sequence>
      <Sequence from={a}><Stage><DesktopShot shot={SHOTS.stock15} dur={dur - a} /></Stage></Sequence>
    </Stage>
  );
};
export const Home15: React.FC<P> = ({ dur }) => {
  const { fps } = useLayout();
  const a = seconds(fps, 1.5);
  return (
    <AbsoluteFill>
      <Sequence durationInFrames={a}><Stage><DesktopShot shot={SHOTS.eod} dur={a} /></Stage></Sequence>
      <Sequence from={a}><Photo src="photos/repair-lab-2-960.webp" dur={dur - a} warm from={1.04} to={1.1} ox={30} oy={45} /></Sequence>
    </AbsoluteFill>
  );
};
