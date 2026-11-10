// Scratch score + sound design, generated in code (no samples, no licences).
// Music notes from the script: act one is a single low tone (with a clock ticking under it);
// a light acoustic rhythm enters exactly on "Same Tuesday" (turnAt); no swell at the logo.
// Sound design: the hook's chime and rewind, the shutter, phone rings, the eleven clicks, the
// "higher plan" error, whooshes on every whip cut, a text ping, the Approved chime, a scan beep,
// a booking ping and the payment ping. All times come from src/captions.ts and src/timing.ts.
// Usage: npx tsx scripts/music.ts  -> public/audio/music-{90,30,15}.wav (music + effects)
import fs from "fs";
import path from "path";
import { FPS, TIMELINE, type Cut } from "../src/captions";
import { APPROVED_IN_SCENE, HOOK_FRAMES, OLD_LOCK_SEC, oldSoftwareClicks } from "../src/timing";

const SR = 44100;
const midi = (n: number) => 440 * Math.pow(2, (n - 69) / 12);
let seed = 12345;
const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff) * 2 - 1;

type Bus = { L: Float32Array; R: Float32Array };
const bus = (n: number): Bus => ({ L: new Float32Array(n), R: new Float32Array(n) });
const add = (b: Bus, i: number, l: number, r = l) => { if (i >= 0 && i < b.L.length) { b.L[i] += l; b.R[i] += r; } };

// ---------- instruments ----------
function pluck(b: Bus, t: number, freq: number, amp: number, pan = 0, len = 1.5, bright = 0.45) {
  const start = Math.floor(t * SR), N = Math.max(2, Math.round(SR / freq));
  const ring = new Float32Array(N);
  for (let i = 0; i < N; i++) ring[i] = rnd();
  let idx = 0;
  for (let i = 0; i < len * SR; i++) {
    const a = ring[idx], c = ring[(idx + 1) % N];
    ring[idx] = (a * (1 - bright * 0.5) + c * (bright * 0.5 + 0.5)) * 0.5 * 0.997;
    idx = (idx + 1) % N;
    const v = a * amp * Math.min(1, i / 30);
    add(b, start + i, v * (1 - pan) , v * (1 + pan));
  }
}
function bell(b: Bus, t: number, f: number, amp: number, decay = 2.6, len = 2.4) {
  const s = Math.floor(t * SR);
  for (let i = 0; i < SR * len; i++) {
    const x = i / SR, env = Math.exp(-x * decay) * Math.min(1, i / 60);
    add(b, s + i, amp * env * (Math.sin(2 * Math.PI * f * x) + 0.3 * Math.sin(2 * Math.PI * f * 2.76 * x) * Math.exp(-x * 4) + 0.15 * Math.sin(2 * Math.PI * f * 5.4 * x) * Math.exp(-x * 7)));
  }
}
function pad(b: Bus, t0: number, t1: number, notes: number[], amp: number) {
  const s = Math.floor(t0 * SR), e = Math.floor(t1 * SR);
  for (let i = s; i < e; i++) {
    const x = i / SR, env = Math.min(1, (x - t0) / 0.8) * Math.min(1, (t1 - x) / 0.8);
    let v = 0;
    for (const n of notes) { const f = midi(n); v += Math.sin(2 * Math.PI * f * x) + 0.4 * Math.sin(2 * Math.PI * f * 1.003 * x + 1) + 0.15 * Math.sin(2 * Math.PI * f * 2 * x); }
    v *= amp * env / notes.length * (0.85 + 0.15 * Math.sin(2 * Math.PI * 0.2 * x));
    add(b, i, v, v * 0.96);
  }
}
function kick(b: Bus, t: number, amp: number) {
  const s = Math.floor(t * SR);
  for (let i = 0; i < SR * 0.35; i++) {
    const x = i / SR, f = 48 + 70 * Math.exp(-x * 30);
    add(b, s + i, amp * Math.exp(-x * 9) * Math.sin(2 * Math.PI * f * x));
  }
}
function noiseBurst(b: Bus, t: number, len: number, amp: number, hp = 0.6, decay = 30, pan = 0) {
  const s = Math.floor(t * SR); let prev = 0;
  for (let i = 0; i < SR * len; i++) {
    const n = rnd(), v = n - hp * prev; prev = n;
    const env = Math.exp(-(i / SR) * decay) * Math.min(1, i / 40);
    add(b, s + i, v * amp * env * (1 - pan), v * amp * env * (1 + pan));
  }
}
// filtered noise swept up or down: whooshes, rewinds
function sweep(b: Bus, t: number, len: number, amp: number, up: boolean) {
  const s = Math.floor(t * SR); let lp = 0, bp = 0;
  for (let i = 0; i < SR * len; i++) {
    const p = i / (SR * len), fc = up ? 300 + 5000 * p * p : 5300 - 5000 * p;
    const k = Math.min(0.95, (2 * Math.PI * fc) / SR);
    const n = rnd(); lp += k * (n - lp); bp += k * (lp - bp);
    const env = Math.sin(Math.PI * Math.min(1, p)) ** 1.5;
    add(b, s + i, (lp - bp) * amp * env * (1.2 - p * 0.4), (lp - bp) * amp * env * (0.8 + p * 0.4));
  }
}
function tone(b: Bus, t: number, len: number, fs: number[], amp: number, attack = 0.005, release = 0.02) {
  const s = Math.floor(t * SR);
  for (let i = 0; i < SR * len; i++) {
    const x = i / SR, env = Math.min(1, x / attack) * Math.min(1, (len - x) / release);
    let v = 0; for (const f of fs) v += Math.sin(2 * Math.PI * f * x);
    add(b, s + i, v * amp * env / fs.length);
  }
}
const shutter = (b: Bus, t: number, len = 1.0) => { for (let k = 0; k < len * 22; k++) noiseBurst(b, t + k / 22 + rnd() * 0.004, 0.05, 0.16, 0.2, 60); kick(b, t + len, 0.25); };
const ringback = (b: Bus, t: number, len = 1.6) => tone(b, t, len, [440, 480], 0.09, 0.02, 0.05);
const click = (b: Bus, t: number) => { noiseBurst(b, t, 0.03, 0.35, 0.9, 140); tone(b, t, 0.03, [180], 0.12); };
const whoosh = (b: Bus, t: number) => sweep(b, t - 0.22, 0.42, 0.5, true);
const ping = (b: Bus, t: number) => { bell(b, t, 1320, 0.07, 6, 0.8); bell(b, t + 0.09, 1760, 0.06, 6, 0.8); };

// ---------- reverb (Schroeder) ----------
function reverb(src: Float32Array, wet: number, size = 1): Float32Array {
  const out = new Float32Array(src.length);
  const combs = [1557, 1617, 1491, 1422].map((d) => ({ buf: new Float32Array(Math.round(d * size)), i: 0 }));
  const aps = [225, 556].map((d) => ({ buf: new Float32Array(d), i: 0 }));
  for (let n = 0; n < src.length; n++) {
    let y = 0;
    for (const c of combs) { const o = c.buf[c.i]; c.buf[c.i] = src[n] + o * 0.82; c.i = (c.i + 1) % c.buf.length; y += o; }
    y /= 4;
    for (const a of aps) { const o = a.buf[a.i]; a.buf[a.i] = y + o * 0.5; a.i = (a.i + 1) % a.buf.length; y = o - 0.5 * y; }
    out[n] = src[n] + y * wet;
  }
  return out;
}

function render(cut: Cut) {
  const tl = TIMELINE[cut];
  const { duration, turnAt } = tl;
  const n = Math.ceil(duration * SR);
  const mus = bus(n), fx = bus(n);
  const seg = (id: string) => tl.segments.find((s) => s.id === id);

  // ===== act one: one low tone, a clock ticking under it =====
  const hookEnd = cut === "90" ? HOOK_FRAMES / FPS : 0;
  for (let i = 0; i < n; i++) {
    const x = i / SR;
    if (x > turnAt + 0.05) break;
    if (x < hookEnd) continue;
    const fadeIn = Math.min(1, (x - hookEnd) / 1.2), fadeOut = Math.min(1, Math.max(0, (turnAt - x) / 0.08));
    const breath = 0.8 + 0.2 * Math.sin(2 * Math.PI * 0.15 * x);
    const v = (Math.sin(2 * Math.PI * 55 * x) * 0.6 + Math.sin(2 * Math.PI * 110 * x) * 0.25 + Math.sin(2 * Math.PI * 165.4 * x) * 0.06) * 0.07 * fadeIn * fadeOut * breath;
    add(mus, i, v);
  }
  for (let t = hookEnd + 0.5; t < turnAt - 0.6; t += 1) noiseBurst(fx, t, 0.02, 0.05, 0.95, 200, 0.3);

  // ===== act two: light acoustic rhythm from turnAt, pad, soft kick =====
  const bpm = 96, beat = 60 / bpm, eighth = beat / 2;
  const chords = [[48, 55, 60, 64, 67], [43, 50, 59, 62, 67], [45, 52, 57, 60, 64], [41, 48, 57, 60, 65]];
  const padNotes = [[60, 64, 67, 72], [59, 62, 67, 74], [57, 60, 64, 72], [57, 60, 65, 69]];
  const pattern = [0, 2, 3, 2, 4, 2, 3, 2];
  const end = duration - 0.25;
  const montage = seg("walk-in"), montageEnd = seg("reports")?.to ?? 0;
  for (let k = 0; ; k++) {
    const t = turnAt + k * eighth;
    if (t >= end - 0.3) break;
    const bar = Math.floor(k / 8) % 4, step = k % 8, ch = chords[bar];
    const tail = Math.min(1, (end - t) / 1.4);
    const AG = 2.6;   // act-two level against the act-one tone
    const vel = AG * (step === 0 ? 0.3 : step % 2 === 0 ? 0.2 : 0.15) * tail * (k < 4 ? 0.6 + 0.1 * k : 1);
    pluck(mus, t, midi(ch[pattern[step]]), vel, step % 2 ? 0.35 : -0.35);
    if (step === 0) pluck(mus, t, midi(ch[0] - 12), AG * 0.2 * tail, 0, 2.0, 0.2);
    if (step % 2 === 1) noiseBurst(mus, t, 0.08, AG * 0.045 * tail, 0.6, 50, step % 4 === 1 ? 0.4 : -0.4);
    if (step === 0 && k >= 16) pad(mus, t, t + beat * 4, padNotes[bar], AG * 0.05 * tail);
    if (k >= 8 && (step === 0 || step === 4)) kick(mus, t, AG * 0.22 * tail);
    // the montage lifts: an octave figure on top
    if (montage && t >= montage.from && t < montageEnd && step % 2 === 0) pluck(mus, t + 0.01, midi(ch[pattern[(step + 2) % 8]] + 12), AG * 0.09 * tail, 0.5, 1.0, 0.6);
  }

  // ===== sound design =====
  const at = (id: string) => seg(id)?.from;
  if (cut === "90") {
    bell(mus, 0.25, midi(88), 0.14); bell(mus, 0.37, midi(95), 0.1);            // hook: the Approved chime
    sweep(fx, HOOK_FRAMES / FPS - 0.75, 0.75, 0.6, false);                      // rewind
    shutter(fx, HOOK_FRAMES / FPS + 0.05, 1.0);
    const c = at("the-customer")!; [0.2, 1.6, 4.2, 5.6].forEach((d) => ringback(fx, c + d, 1.0));
    tone(fx, c + 3.0, 0.35, [480, 620], 0.06);                                  // no answer
    tone(fx, c + 6.9, 0.25, [950, 1400, 1800], 0.05);                           // voicemail beep
    const o = at("old-software")!; oldSoftwareClicks(false).forEach((f) => click(fx, o + f / FPS));
    tone(fx, o + OLD_LOCK_SEC, 0.25, [220, 233], 0.12);                          // error bonk
    const d = at("the-drawer")!; noiseBurst(fx, d + 0.2, 0.4, 0.12, 0.1, 6);    // drawer slides
    const sb = at("support-and-bill")!; for (let k = 0; k < 6; k++) tone(fx, sb + k * 0.42, 0.38, [midi(64 + [0, 4, 7, 4, 0, -5][k])], 0.03); // hold music
    ping(fx, sb + 2.8);                                                         // the renewal email
  }
  if (cut === "30") {
    ringback(fx, 0.2, 1.4); ringback(fx, 2.0, 1.0); tone(fx, 3.3, 0.35, [480, 620], 0.06);
    const o = at("clicks-renewal")!; oldSoftwareClicks(true).forEach((f) => click(fx, o + f / FPS)); ping(fx, o + 1.95);
  }
  if (cut === "15") { ringback(fx, 0.15, 1.2); tone(fx, 1.7, 0.35, [480, 620], 0.06); }
  shutter(fx, turnAt + 0.45, 1.1);                                              // the turn: the shutter goes up again
  sweep(fx, turnAt - 0.1, 0.5, 0.25, true);

  // approval: the text arrives, then Approved
  const ap = seg("the-approval") ?? seg("one-text");
  if (ap) ping(fx, ap.from + 0.25);
  const apScene = seg("the-approval") ?? seg("approved");
  if (apScene) {
    const tA = cut === "15" ? apScene.from + 0.1 : apScene.from + APPROVED_IN_SCENE[cut];
    bell(mus, tA, midi(88), 0.16); bell(mus, tA + 0.12, midi(95), 0.12);
  }
  // whooshes on every whip cut in act two
  const whips = ["intake", "the-approval", "the-part", "walk-in", "booking", "membership", "reports", "close-of-job", "approved", "home"];
  for (const s of tl.segments) if (s.from > turnAt + 1 && whips.includes(s.id)) whoosh(fx, s.from);
  if (seg("walk-in")) { tone(fx, seg("walk-in")!.from + 1.0, 0.09, [2400], 0.08); tone(fx, seg("walk-in")!.from + 1.12, 0.09, [3200], 0.06); } // QR scan beep
  if (seg("booking")) ping(fx, seg("booking")!.from + 1.6);
  const paid = tl.segments.flatMap((s) => s.onScreen ?? []).find((o) => /^Paid/.test(o.text));
  if (paid) { bell(fx, paid.from, midi(84), 0.08, 5, 1); bell(fx, paid.from + 0.07, midi(88), 0.08, 5, 1); bell(fx, paid.from + 0.14, midi(91), 0.08, 5, 1.2); }

  // ===== mix =====
  const mL = reverb(mus.L, 0.35, 1), mR = reverb(mus.R, 0.35, 1.03);
  const fL = reverb(fx.L, 0.15, 0.8), fR = reverb(fx.R, 0.15, 0.83);
  const L = new Float32Array(n), R = new Float32Array(n);
  let peak = 0;
  for (let i = 0; i < n; i++) { L[i] = mL[i] + fL[i]; R[i] = mR[i] + fR[i]; peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i])); }
  const g = peak > 0.95 ? 0.95 / peak : 1;
  const out = Buffer.alloc(44 + n * 4);
  out.write("RIFF", 0); out.writeUInt32LE(36 + n * 4, 4); out.write("WAVE", 8); out.write("fmt ", 12);
  out.writeUInt32LE(16, 16); out.writeUInt16LE(1, 20); out.writeUInt16LE(2, 22); out.writeUInt32LE(SR, 24);
  out.writeUInt32LE(SR * 4, 28); out.writeUInt16LE(4, 32); out.writeUInt16LE(16, 34); out.write("data", 36); out.writeUInt32LE(n * 4, 40);
  for (let i = 0; i < n; i++) {
    const fade = Math.min(1, (duration - i / SR) / 0.6);
    out.writeInt16LE(Math.round(Math.tanh(L[i] * g) * fade * 32767), 44 + i * 4);
    out.writeInt16LE(Math.round(Math.tanh(R[i] * g) * fade * 32767), 46 + i * 4);
  }
  const dir = path.join(__dirname, "..", "public", "audio");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `music-${cut}.wav`), out);
  console.log(`music-${cut}.wav  ${duration}s  rhythm enters at ${turnAt}s`);
}

for (const cut of ["90", "30", "15"] as const) render(cut);
