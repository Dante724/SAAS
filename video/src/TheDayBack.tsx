import React, { useEffect, useState } from "react";
import { AbsoluteFill, Audio, Sequence, continueRender, delayRender, staticFile } from "remotion";
import { loadBrandFonts } from "./brand";
import { Captions } from "./CaptionLayer";
import { FPS, TIMELINE, type Aspect, type Cut, type SceneId, type Segment } from "./captions";
import { HOOK_FRAMES } from "./timing";
import { ColdOpen, Hook, Customer, Drawer, OldSoftware, Support, Turn } from "./scenes/ActOne";
import { Approval, Approval30, Approved15, Close, Close30, DanisLine, Home15, Intake, Part, Text15 } from "./scenes/ActTwo";
import { EndCard } from "./scenes/EndCard";
import { Booking, CheckIn, Membership, Reports } from "./scenes/Montage";
import { Chip } from "./ui";
import { Grain, Letterbox, LightLeak, Vignette, Whip } from "./cinema";

export type Props = { cut: Cut; aspect: Aspect; voiceoverPath: string; musicPath: string };

const src = (p: string) => (/^https?:\/\//.test(p) ? p : staticFile(p));

const scene = (id: SceneId, dur: number): React.ReactNode => {
  switch (id) {
    case "hook": return <Hook dur={dur} />;
    case "coldOpen": return <ColdOpen dur={dur} />;
    case "customer": return <Customer dur={dur} />;
    case "oldSoftware": return <OldSoftware dur={dur} />;
    case "drawer": return <Drawer dur={dur} />;
    case "support": return <Support dur={dur} />;
    case "turn": return <Turn dur={dur} />;
    case "intake": return <Intake dur={dur} />;
    case "approval": return <Approval dur={dur} />;
    case "part": return <Part dur={dur} />;
    case "close": return <Close dur={dur} />;
    case "danisLine": return <DanisLine dur={dur} />;
    case "checkin": return <CheckIn dur={dur} />;
    case "booking": return <Booking dur={dur} />;
    case "membership": return <Membership dur={dur} />;
    case "reports": return <Reports dur={dur} />;
    case "endCard": return <EndCard dur={dur} sub="Free trial · No contract" fan={60} />;
    case "redial": return <Customer dur={dur} variant="30" />;
    case "clicksRenewal": return <OldSoftware dur={dur} short />;
    case "approval30": return <Approval30 dur={dur} />;
    case "close30": return <Close30 dur={dur} />;
    case "endCard30": return <EndCard dur={dur} fan={40} />;
    case "calls15": return <Customer dur={dur} variant="15" />;
    case "text15": return <Text15 dur={dur} />;
    case "approved15": return <Approved15 dur={dur} />;
    case "home15": return <Home15 dur={dur} />;
    case "end15": return <EndCard dur={dur} showTrades={false} />;
  }
};

// Act-two scenes that whip into each other.
const WHIP: SceneId[] = ["intake", "approval", "part", "checkin", "booking", "membership", "reports", "close", "approval30", "close30", "text15", "approved15"];

// Scenes that draw their own headline text (the Tekly wordmark, the end card's tekly.app).
const DRAWS_OWN_TEXT: SceneId[] = ["turn", "endCard", "endCard30", "end15"];

export const TheDayBack: React.FC<Props> = ({ cut, voiceoverPath, musicPath }) => {
  const [handle] = useState(() => delayRender("brand fonts"));
  useEffect(() => { loadBrandFonts().then(() => continueRender(handle)); }, [handle]);
  const tl = TIMELINE[cut];
  const f = (s: number) => Math.round(s * FPS);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <Sequence from={f(tl.turnAt) + 30} layout="none"><AbsoluteFill style={{ background: "#eef2f9" }} /></Sequence>
      {tl.segments.map((s: Segment, i) => {
        const dur = f(s.to) - f(s.from);
        const body = scene(s.scene, dur);
        const prevWhip = i > 0 && WHIP.includes(tl.segments[i - 1].scene);
        return (
          <Sequence key={s.id} name={s.id} from={f(s.from)} durationInFrames={dur}>
            {WHIP.includes(s.scene)
              ? <Whip dur={dur} inF={prevWhip ? 9 : 0} outF={i + 1 < tl.segments.length && WHIP.includes(tl.segments[i + 1].scene) ? 7 : 0} dir={i % 2 ? -1 : 1}>{body}</Whip>
              : body}
          </Sequence>
        );
      })}
      {/* the film layer */}
      <Sequence durationInFrames={f(tl.turnAt)} layout="none"><Vignette strength={0.55} /><Grain amount={0.09} /></Sequence>
      <Sequence from={f(tl.turnAt)} layout="none"><Vignette strength={0.16} /><Grain amount={0.045} /></Sequence>
      <Letterbox from={cut === "90" ? HOOK_FRAMES - 10 : 0} until={f(tl.turnAt) + 14} />
      <LightLeak at={f(tl.turnAt) + 26} len={44} />
      {tl.segments.filter((s) => !DRAWS_OWN_TEXT.includes(s.scene)).flatMap((s) => (s.onScreen ?? []).map((o, i) => (
        <Sequence key={`${s.id}-t${i}`} from={f(o.from)} durationInFrames={f(o.to) - f(o.from)} layout="none">
          <Chip text={o.text} dur={f(o.to) - f(o.from)} strong={o.strong} dark={o.from < tl.turnAt}
            tone={/no answer|voicemail|\+22%|Call 3/.test(o.text) ? "red" : /Approved|Paid|reserved/.test(o.text) ? "green" : o.from >= tl.turnAt ? "blue" : undefined} />
        </Sequence>
      )))}
      <Captions cut={cut} />
      {musicPath ? <Audio src={src(musicPath)} volume={0.55} /> : null}
      {voiceoverPath ? <Audio src={src(voiceoverPath)} /> : null}
    </AbsoluteFill>
  );
};
