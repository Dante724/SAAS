import fs from "fs";
import path from "path";

// The ffmpeg/ffprobe Remotion ships with its compositor (full build: atempo, amix, tile, …).
const bin = (name: "ffmpeg" | "ffprobe") => {
  const base = path.join(__dirname, "..", "node_modules", "@remotion");
  for (const d of fs.readdirSync(base).filter((x) => x.startsWith("compositor-"))) {
    const p = path.join(base, d, name);
    if (fs.existsSync(p)) return p;
  }
  throw new Error(`${name} not found under node_modules/@remotion/compositor-*`);
};
export const ffmpegPath = () => bin("ffmpeg");
export const ffprobePath = () => bin("ffprobe");
// the compositor binaries need their bundled dylibs next to them
export const ffEnv = () => {
  const dir = path.dirname(bin("ffmpeg"));
  return { ...process.env, DYLD_LIBRARY_PATH: dir, LD_LIBRARY_PATH: dir };
};
