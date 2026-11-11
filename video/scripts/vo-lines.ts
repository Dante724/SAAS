// Prints the voice-over lines from captions.ts as JSON (for the scratch TTS step).
import { TIMELINE } from "../src/captions";
const out: { name: string; text: string; speaker: string; from: number; to: number }[] = [];
for (const cut of ["90", "30"] as const) for (const s of TIMELINE[cut].segments) if (s.vo)
  out.push({ name: `${cut}-${s.id}`, text: s.vo.replace("$140", "a hundred and forty dollars").replace(/teklyGO/g, "Tekly Go"), speaker: s.speaker ?? "dani", from: s.from, to: s.to });
console.log(JSON.stringify(out, null, 1));
