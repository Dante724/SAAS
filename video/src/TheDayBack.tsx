import React, { useEffect, useState } from "react";
import { AbsoluteFill, Audio, Sequence, continueRender, delayRender, staticFile } from "remotion";
import { loadBrandFonts } from "./brand";
import { Captions } from "./CaptionLayer";
import { FPS, TIMELINE, type Aspect, type Cut, type SceneId, type Segment } from "./captions";
import { ColdOpen, Customer, Drawer, OldSoftware, Support, Turn } from "./scenes/ActOne";
import { Approval, Approval30, Approved15, Close, Close30, DanisLine, Home15, Intake, Part, Text15 } from "./scenes/ActTwo";
import { EndCard } from "./scenes/EndCard";
import { Chip } from "./ui";

export type Props = { cut: Cut; aspect: Aspect; voiceoverPath: string; musicPath: string };

const src = (p: string) => (/^https?:\/\//.test(p) ? p : staticFile(p));

const scene = (id: SceneId, dur: number): React.ReactNode => {
  switch (id) {
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
    case "endCard": return <EndCard dur={dur} sub="Free trial · No contract" />;
    case "redial": return <Customer dur={dur} variant="30" />;
    case "clicksRenewal": return <OldSoftware dur={dur} short />;
    case "approval30": return <Approval30 dur={dur} />;
    case "close30": return <Close30 dur={dur} />;
    case "endCard30": return <EndCard dur={dur} />;
    case "calls15": return <Customer dur={dur} variant="15" />;
    case "text15": return <Text15 dur={dur} />;
    case "approved15": return <Approved15 dur={dur} />;
    case "home15": return <Home15 dur={dur} />;
    case "end15": return <EndCard dur={dur} showTrades={false} />;
  }
};

// Scenes that draw their own headline text (the Tekly wordmark, the end card's tekly.app).
const DRAWS_OWN_TEXT: SceneId[] = ["turn", "endCard", "endCard30", "end15"];

export const TheDayBack: React.FC<Props> = ({ cut, voiceoverPath, musicPath }) => {
  const [handle] = useState(() => delayRender("brand fonts"));
  useEffect(() => { loadBrandFonts().then(() => continueRender(handle)); }, [handle]);
  const tl = TIMELINE[cut];
  const f = (s: number) => Math.round(s * FPS);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {tl.segments.map((s: Segment) => (
        <Sequence key={s.id} name={s.id} from={f(s.from)} durationInFrames={f(s.to) - f(s.from)}>
          {scene(s.scene, f(s.to) - f(s.from))}
        </Sequence>
      ))}
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
