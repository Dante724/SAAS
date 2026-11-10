// Scratch score, generated (no samples, no licences). Per the script's music notes:
// act one is a single low tone; a light acoustic rhythm enters exactly on "Same Tuesday" (turnAt);
// a soft chime when the board flips to Approved; no swell at the logo, just a short tail.
// Usage: npx tsx scripts/music.ts  -> public/audio/music-{90,30,15}.wav
import fs from "fs";
import path from "path";
import { TIMELINE, type Cut } from "../src/captions";

const SR = 44100;
const CHIME_AT: Record<Cut, number> = { "90": 60.0, "30": 17.0, "15": 7.0 };

const midi = (n: number) => 440 * Math.pow(2, (n - 69) / 12);

// Karplus-Strong plucked string, written into buf at time t
function pluck(buf: Float32Array, t: number, freq: number, amp: number, len = 1.6, bright = 0.5) {
  const start = Math.floor(t * SR);
  const N = Math.max(2, Math.round(SR / freq));
  const ring = new Float32Array(N);
  let seed = Math.floor(freq * 1000 + t * 7919);
  const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff) * 2 - 1;
  for (let i = 0; i < N; i++) ring[i] = rnd();
  let idx = 0;
  const total = Math.floor(len * SR);
  for (let i = 0; i < total && start + i < buf.length; i++) {
    const a = ring[idx], b = ring[(idx + 1) % N];
    const v = (a * (1 - bright * 0.5) + b * (bright * 0.5 + 0.5)) * 0.5 * 0.996 + 0.002 * a;
    ring[idx] = v;
    idx = (idx + 1) % N;
    const env = i < 40 ? i / 40 : 1;
    buf[start + i] += a * amp * env;
  }
}

function shaker(buf: Float32Array, t: number, amp: number) {
  const start = Math.floor(t * SR);
  let prev = 0;
  let seed = Math.floor(t * 104729);
  for (let i = 0; i < SR * 0.09 && start + i < buf.length; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    const n = (seed / 0x7fffffff) * 2 - 1;
    const hp = n - prev; prev = n;
    const env = Math.exp(-i / (SR * 0.018)) * Math.min(1, i / 60);
    buf[start + i] += hp * amp * env;
  }
}

function bell(buf: Float32Array, t: number, f: number, amp: number) {
  const start = Math.floor(t * SR);
  for (let i = 0; i < SR * 2.2 && start + i < buf.length; i++) {
    const x = i / SR;
    const env = Math.exp(-x * 2.6) * Math.min(1, i / 80);
    buf[start + i] += amp * env * (Math.sin(2 * Math.PI * f * x) + 0.35 * Math.sin(2 * Math.PI * f * 2.76 * x) * Math.exp(-x * 4));
  }
}

function render(cut: Cut) {
  const { duration, turnAt } = TIMELINE[cut];
  const n = Math.ceil(duration * SR);
  const L = new Float32Array(n), R = new Float32Array(n);

  // act one: one low tone (A1 + octave), slow breathing, ending right at the turn
  for (let i = 0; i < n; i++) {
    const x = i / SR;
    if (x > turnAt + 0.05) break;
    const fadeIn = Math.min(1, x / 1.5), fadeOut = Math.min(1, Math.max(0, (turnAt - x) / 0.08));
    const breath = 0.8 + 0.2 * Math.sin(2 * Math.PI * 0.15 * x);
    const v = (Math.sin(2 * Math.PI * 55 * x) * 0.6 + Math.sin(2 * Math.PI * 110 * x) * 0.25 + Math.sin(2 * Math.PI * 165.4 * x) * 0.06) * 0.16 * fadeIn * fadeOut * breath;
    L[i] += v; R[i] += v;
  }

  // act two: light acoustic rhythm from turnAt. 96 bpm, eighth-note fingerpicking, C – G – Am – F.
  const bpm = 96, eighth = 60 / bpm / 2;
  const chords = [[48, 55, 60, 64, 67], [43, 50, 59, 62, 67], [45, 52, 57, 60, 64], [41, 48, 57, 60, 65]];
  const pattern = [0, 2, 3, 2, 4, 2, 3, 2];
  const end = duration - 0.2;
  for (let k = 0; ; k++) {
    const t = turnAt + k * eighth;
    if (t >= end - 0.3) break;
    const bar = Math.floor(k / 8) % 4, step = k % 8;
    const ch = chords[bar];
    const tail = Math.min(1, (end - t) / 1.6);         // short, flat tail; no swell
    const vel = (step === 0 ? 0.34 : step % 2 === 0 ? 0.22 : 0.17) * tail * (k < 4 ? 0.6 + 0.1 * k : 1);
    const note = midi(ch[pattern[step]] + 12 * (pattern[step] === 0 ? 0 : 0));
    const buf = step % 2 === 0 ? L : R;
    pluck(buf, t, note, vel, 1.4, 0.4);
    pluck(step % 2 === 0 ? R : L, t + 0.004, note, vel * 0.55, 1.4, 0.4);
    if (step === 0) { pluck(L, t, midi(ch[0] - 12), 0.22 * tail, 2.0, 0.2); pluck(R, t, midi(ch[0] - 12), 0.22 * tail, 2.0, 0.2); }
    if (step % 2 === 1) { shaker(L, t, 0.05 * tail); shaker(R, t + 0.003, 0.045 * tail); }
  }

  // the approval chime
  const c = CHIME_AT[cut];
  for (const [dt, note, a] of [[0, 88, 0.16], [0.12, 95, 0.12]] as const) { bell(L, c + dt, midi(note), a); bell(R, c + dt, midi(note), a); }

  // master: soft limiter, 0.6 s fade at the very end
  let peak = 0;
  for (let i = 0; i < n; i++) { peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i])); }
  const g = peak > 0.9 ? 0.9 / peak : 1;
  const out = Buffer.alloc(44 + n * 4);
  out.write("RIFF", 0); out.writeUInt32LE(36 + n * 4, 4); out.write("WAVE", 8); out.write("fmt ", 12);
  out.writeUInt32LE(16, 16); out.writeUInt16LE(1, 20); out.writeUInt16LE(2, 22); out.writeUInt32LE(SR, 24);
  out.writeUInt32LE(SR * 4, 28); out.writeUInt16LE(4, 32); out.writeUInt16LE(16, 34); out.write("data", 36); out.writeUInt32LE(n * 4, 40);
  for (let i = 0; i < n; i++) {
    const x = i / SR, fade = Math.min(1, (duration - x) / 0.6);
    out.writeInt16LE(Math.round(Math.tanh(L[i] * g) * fade * 32767), 44 + i * 4);
    out.writeInt16LE(Math.round(Math.tanh(R[i] * g) * fade * 32767), 46 + i * 4);
  }
  const dir = path.join(__dirname, "..", "public", "audio");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `music-${cut}.wav`), out);
  console.log(`music-${cut}.wav  ${duration}s  rhythm enters at ${turnAt}s  chime at ${c}s`);
}

for (const cut of ["90", "30", "15"] as const) render(cut);
