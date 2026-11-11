// Checks before review:
//  1. every captured clip plays (valid duration, decodes without errors)
//  2. captions match the script's timecodes within 0.2 s (on the 30 fps frame grid),
//     and each voice-over line actually starts within 0.2 s of its caption
//  3. rendered durations match the timeline (1:48, 0:30, 0:15)
// Usage: npx tsx scripts/verify.ts
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";
import { FPS, TIMELINE, captionCues, type Aspect, type Cut } from "../src/captions";
import { ffEnv, ffmpegPath, ffprobePath } from "./ffmpeg";
import { outName } from "./render-all";

const root = path.join(__dirname, "..");
let failures = 0;
const ok = (cond: boolean, msg: string) => { console.log(`${cond ? "  ok " : "FAIL "} ${msg}`); if (!cond) failures++; };
const probe = (f: string) => parseFloat(execFileSync(ffprobePath(), ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], { env: ffEnv() }).toString());

console.log("1. captured clips");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "public/clips/manifest.json"), "utf8"));
for (const c of Object.values(manifest.clips) as { file: string }[]) {
  const f = path.join(root, "public/clips", c.file);
  // decode every frame (ffprobe -count_frames) and compare with the expected count
  let err = "", frames = 0;
  try {
    frames = parseInt(execFileSync(ffprobePath(), ["-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", f],
      { env: ffEnv(), stdio: ["ignore", "pipe", "pipe"] }).toString(), 10);
  } catch (e) { err = String((e as { stderr?: Buffer }).stderr ?? e); }
  const d = probe(f);
  if (!err && frames < d * 20) err = `only ${frames} frames decoded`;
  ok(d > 2 && !err.trim(), `${c.file} plays (${d.toFixed(1)} s, ${frames} frames${err.trim() ? `, errors: ${err.trim().slice(0, 80)}` : ""})`);
}

console.log("2. captions vs script timecodes");
const pcm = (file: string) => {
  const b = fs.readFileSync(file);
  let off = 12, sr = 48000, ch = 2;
  while (off < b.length - 8) {
    const id = b.toString("ascii", off, off + 4), size = b.readUInt32LE(off + 4);
    if (id === "fmt ") { ch = b.readUInt16LE(off + 10); sr = b.readUInt32LE(off + 12); }
    if (id === "data") return { sr, ch, data: b.subarray(off + 8, off + 8 + size) };
    off += 8 + size;
  }
  throw new Error("bad wav");
};
// first moment after `t` where the voice is clearly audible (10 ms windows)
const onset = (w: ReturnType<typeof pcm>, t: number, until: number) => {
  const win = Math.round(w.sr / 100);
  for (let s = Math.round(t * w.sr); s < until * w.sr; s += win) {
    let sum = 0;
    for (let k = 0; k < win; k++) { const v = w.data.readInt16LE(((s + k) * w.ch) * 2) / 32768; sum += v * v; }
    if (Math.sqrt(sum / win) > 0.02) return s / w.sr;
  }
  return NaN;
};
for (const cut of ["90", "30", "15"] as Cut[]) {
  const cues = captionCues(cut);
  const vo = path.join(root, "public/audio", `vo-${cut}.wav`);
  const wav = cut !== "15" && fs.existsSync(vo) ? pcm(vo) : null;
  for (const s of TIMELINE[cut].segments) {
    const mine = cues.filter((c) => c.segment === s.id);
    if (!mine.length) continue;
    const start = Math.round(mine[0].from * FPS) / FPS, end = Math.round(mine[mine.length - 1].to * FPS) / FPS;
    const dStart = Math.abs(start - s.from), dEnd = Math.abs(end - s.to);
    let line = `${cut}s ${s.id}: caption ${start.toFixed(2)}–${end.toFixed(2)} vs script ${s.from}–${s.to}`;
    let good = dStart <= 0.2 && dEnd <= 0.2;
    if (wav) {
      const on = onset(wav, Math.max(0, s.from - 0.3), s.to);
      line += `, voice starts ${on.toFixed(2)}`;
      good = good && Math.abs(on - s.from) <= 0.2;
    }
    ok(good, line);
  }
}

console.log("3. rendered durations");
const want: Record<Cut, number> = { "90": TIMELINE["90"].duration, "30": TIMELINE["30"].duration, "15": TIMELINE["15"].duration };
for (const cut of ["90", "30", "15"] as Cut[]) for (const aspect of ["16:9", "1:1", "9:16"] as Aspect[]) {
  const f = path.join(root, "out", outName(cut, aspect));
  if (!fs.existsSync(f)) { ok(false, `${outName(cut, aspect)} missing`); continue; }
  const d = probe(f);
  ok(Math.abs(d - want[cut]) < 0.1, `${outName(cut, aspect)} ${Math.floor(d / 60)}:${(d % 60).toFixed(2).padStart(5, "0")} (want ${want[cut]} s)`);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
