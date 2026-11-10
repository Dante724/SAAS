// Renders all nine versions (3 cuts × 3 aspects) to out/.
// Usage: npx tsx scripts/render-all.ts [cut ...]   e.g. `npx tsx scripts/render-all.ts 15`
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";
import type { Aspect, Cut } from "../src/captions";

const root = path.join(__dirname, "..");
const cuts = (process.argv.slice(2).length ? process.argv.slice(2) : ["90", "30", "15"]) as Cut[];
const aspects: Aspect[] = ["16:9", "1:1", "9:16"];
const audio = (p: string) => (fs.existsSync(path.join(root, "public", p)) ? p : "");

export const outName = (cut: Cut, aspect: Aspect) => `tekly-the-day-back-${cut}s-${aspect.replace(":", "x")}.mp4`;

if (require.main === module) {
  fs.mkdirSync(path.join(root, "out"), { recursive: true });
  for (const cut of cuts) for (const aspect of aspects) {
    const props = {
      cut, aspect,
      voiceoverPath: cut === "15" ? "" : audio(`audio/vo-${cut}.wav`),   // the 15 s cut is captions only
      musicPath: audio(`audio/music-${cut}.wav`),
    };
    const out = path.join("out", outName(cut, aspect));
    const t0 = Date.now();
    console.log(`rendering ${out} …`);
    execFileSync("npx", ["remotion", "render", "src/index.ts", "TheDayBack", out, `--props=${JSON.stringify(props)}`, "--log=error"],
      { cwd: root, stdio: "inherit" });
    console.log(`  done in ${((Date.now() - t0) / 1000).toFixed(0)} s`);
  }
}
