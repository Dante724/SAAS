// Lays the voice-over lines onto each cut's timeline: each line starts on its segment's
// script timecode (+ a short breath) and is sped up just enough to end before the next segment.
// Usage: npx tsx scripts/vo-mix.ts <dir-with-<cut>-<segment>.wav>  -> public/audio/vo-{90,30}.wav
// Final voice-over: record one WAV per cut and pass it as `voiceoverPath` instead.
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";
import { TIMELINE } from "../src/captions";
import { ffEnv, ffmpegPath } from "./ffmpeg";

const dir = process.argv[2];
if (!dir) throw new Error("usage: vo-mix.ts <dir>");

const wavSeconds = (file: string) => {
  const b = fs.readFileSync(file);
  const ch = b.readUInt16LE(22), sr = b.readUInt32LE(24), bits = b.readUInt16LE(34);
  let off = 12;
  while (off < b.length - 8) {
    const id = b.toString("ascii", off, off + 4), size = b.readUInt32LE(off + 4);
    if (id === "data") return size / (sr * ch * (bits / 8));
    off += 8 + size;
  }
  throw new Error(`no data chunk in ${file}`);
};

// where speech starts in a TTS line (skip its leading silence)
const leadIn = (file: string) => {
  const b = fs.readFileSync(file);
  let off = 12, fmt = 1, ch = 1, sr = 24000, bits = 16;
  while (off < b.length - 8) {
    const id = b.toString("ascii", off, off + 4), size = b.readUInt32LE(off + 4);
    if (id === "fmt ") { fmt = b.readUInt16LE(off + 8); ch = b.readUInt16LE(off + 10); sr = b.readUInt32LE(off + 12); bits = b.readUInt16LE(off + 22); }
    if (id === "data") {
      const step = (bits / 8) * ch, d = off + 8;
      for (let i = 0; i * step < size; i++) {
        const v = fmt === 3 ? b.readFloatLE(d + i * step) : b.readInt16LE(d + i * step) / 32768;
        if (Math.abs(v) > 0.03) return Math.max(0, i / sr - 0.03);
      }
      return 0;
    }
    off += 8 + size;
  }
  return 0;
};

const BREATH = 0.08, TAIL = 0.1;
for (const cut of ["90", "30"] as const) {
  const segs = TIMELINE[cut].segments.filter((s) => s.vo);
  const args: string[] = ["-y"];
  const filters: string[] = [];
  segs.forEach((s, i) => {
    const file = path.join(dir, `${cut}-${s.id}.wav`);
    const lead = leadIn(file);
    const len = wavSeconds(file) - lead;
    const room = s.to - s.from - BREATH - TAIL;
    const tempo = Math.min(1.4, Math.max(1, len / room));
    args.push("-i", file);
    filters.push(`[${i}:a]atrim=start=${lead.toFixed(3)},asetpts=PTS-STARTPTS,aresample=48000,atempo=${tempo.toFixed(3)},adelay=${Math.round((s.from + BREATH) * 1000)}:all=1[a${i}]`);
    console.log(`${cut} ${s.id.padEnd(18)} ${len.toFixed(2)}s in ${room.toFixed(2)}s${tempo > 1 ? `  x${tempo.toFixed(2)}` : ""}${len / tempo > room + 0.05 ? "  ! still long" : ""}`);
  });
  filters.push(`${segs.map((_, i) => `[a${i}]`).join("")}amix=inputs=${segs.length}:normalize=0,apad=whole_dur=${TIMELINE[cut].duration},atrim=0:${TIMELINE[cut].duration}[out]`);
  const out = path.join(__dirname, "..", "public", "audio", `vo-${cut}.wav`);
  execFileSync(ffmpegPath(), [...args, "-filter_complex", filters.join(";"), "-map", "[out]", "-ac", "2", "-ar", "48000", out], { stdio: "pipe", env: ffEnv() });
  console.log(`-> ${out}`);
}
