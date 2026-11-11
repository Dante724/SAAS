import React from "react";
import { Composition } from "remotion";
import { FPS, SIZES, TIMELINE } from "./captions";
import { TheDayBack, type Props } from "./TheDayBack";

const defaults: Props = { cut: "90", aspect: "16:9", voiceoverPath: "audio/vo-90.wav", musicPath: "audio/music-90.wav" };

export const Root: React.FC = () => (
  <Composition
    id="TheDayBack"
    component={TheDayBack as unknown as React.FC<Record<string, unknown>>}
    fps={FPS}
    durationInFrames={TIMELINE["90"].duration * FPS}
    width={1920}
    height={1080}
    defaultProps={defaults as unknown as Record<string, unknown>}
    calculateMetadata={({ props }) => {
      const p = props as unknown as Props;
      return { durationInFrames: Math.round(TIMELINE[p.cut].duration * FPS), ...SIZES[p.aspect] };
    }}
  />
);
