// Act two: the same Tuesday in Tekly. Every screen here is a real recording of the app
// (video/capture/record.ts, seeded demo shop), framed and zoomed toward the moment that matters.
import React from "react";
import { AbsoluteFill, Sequence, interpolate, spring, useCurrentFrame } from "remotion";
import manifest from "../../public/clips/manifest.json";
import { SHOTS, type Shot } from "../shots";
import { BrowserFrame, ClipView, PhoneFrame, Photo, Stage, clamp, useLayout } from "../ui";

type P = { dur: number };
type ClipInfo = { file: string; width: number; height: number; durationSec: number };
const info = (name: string): ClipInfo => {
  const c = (manifest as unknown as { clips: Record<string, ClipInfo> }).clips[name];
  if (!c) throw new Error(`clip ${name} missing from public/clips/manifest.json — run npm run capture`);
  return c;
};

// The desktop app in a browser window. On square/vertical the box is narrower, so the zoom works harder.
const DesktopShot: React.FC<{ shot: Shot; dur: number; enter?: boolean }> = ({ shot, dur, enter = true }) => {
  const f = useCurrentFrame();
  const { u, aspect, fps } = useLayout();
  const c = info(shot.clip);
  const box = aspect === "wide" ? { w: 1400 * u, h: 875 * u } : aspect === "square" ? { w: 960 * u, h: 780 * u } : { w: 1000 * u, h: 1240 * u };
  const extra = 1;   // on square/vertical the cover-fit crop already brings the camera closer
  const zoom = (shot.zoom ?? [{ t: 0, s: 1, x: 0.5, y: 0.5 }]).map((z) => ({ ...z, s: z.s * extra }));
  const s = enter ? spring({ frame: f, fps, config: { damping: 20, stiffness: 120 } }) : 1;
  const out = interpolate(f, [dur - 6, dur], [1, 0.0], clamp);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", paddingTop: aspect === "tall" ? 0 : 30 * u }}>
      <div style={{ transform: `translateY(${(1 - s) * 40 * u}px) scale(${0.97 + 0.03 * s})`, opacity: Math.min(s, shot.fadeOut ? out : 1) }}>
        <BrowserFrame w={box.w} h={box.h}>
          <ClipView src={`clips/${c.file}`} clipW={c.width} clipH={c.height} boxW={box.w} boxH={box.h} startFrom={shot.from} rate={shot.rate} clipDur={c.durationSec} zoom={zoom} />
        </BrowserFrame>
      </div>
    </AbsoluteFill>
  );
};

const MobileShot: React.FC<{ shot: Shot; dur: number; x?: string }> = ({ shot, x = "50%" }) => {
  const f = useCurrentFrame();
  const { u, aspect, fps } = useLayout();
  const c = info(shot.clip);
  const h = (aspect === "tall" ? 1240 : 880) * u;
  const w = h * (390 / 844);
  const s = spring({ frame: f, fps, config: { damping: 20, stiffness: 120 } });
  return (
    <div style={{ position: "absolute", left: x, top: "50%", transform: `translate(-50%, -50%) translateY(${(1 - s) * 60 * u}px)`, opacity: s }}>
      <PhoneFrame h={h}>
        <ClipView src={`clips/${c.file}`} clipW={c.width} clipH={c.height} boxW={w} boxH={h} startFrom={shot.from} rate={shot.rate} clipDur={c.durationSec} zoom={shot.zoom} />
      </PhoneFrame>
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
export const Close: React.FC<P> = ({ dur }) => <Stage><DesktopShot shot={SHOTS.invoice} dur={dur} /></Stage>;

// 1:20 Dani's line: the end-of-day board, mostly green, then the shop, warm
export const DanisLine: React.FC<P> = ({ dur }) => {
  const { fps } = useLayout();
  const half = seconds(fps, 3.2);
  return (
    <AbsoluteFill>
      <Sequence durationInFrames={half}><Stage><DesktopShot shot={SHOTS.eod} dur={half} /></Stage></Sequence>
      <Sequence from={half}><Photo src="photos/repair-lab-2-960.webp" dur={dur - half} warm from={1.02} to={1.1} ox={30} oy={45} /></Sequence>
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
