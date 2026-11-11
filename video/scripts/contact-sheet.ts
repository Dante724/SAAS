// Contact sheet: one frame from the middle of every scene, for every rendered version,
// laid out with the script's timecode and VO line. Writes out/contact-sheet.html and .png.
// Usage: npx tsx scripts/contact-sheet.ts   (after render-all)
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";
import { chromium } from "playwright";
import { TIMELINE, type Aspect, type Cut } from "../src/captions";
import { ffEnv, ffmpegPath } from "./ffmpeg";
import { outName } from "./render-all";

const root = path.join(__dirname, "..");
const outDir = path.join(root, "out");
const frames = path.join(outDir, "contact-frames");
fs.mkdirSync(frames, { recursive: true });

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");

const sections: string[] = [];
for (const cut of ["90", "30", "15"] as Cut[]) for (const aspect of ["16:9", "1:1", "9:16"] as Aspect[]) {
  const video = path.join(outDir, outName(cut, aspect));
  if (!fs.existsSync(video)) continue;
  const cells: string[] = [];
  for (const s of TIMELINE[cut].segments) {
    const at = s.from + (s.to - s.from) * 0.62;
    const png = `${cut}-${aspect.replace(":", "x")}-${s.id}.jpg`;
    execFileSync(ffmpegPath(), ["-y", "-ss", at.toFixed(2), "-i", video, "-frames:v", "1", "-vf", "scale=-2:360", "-q:v", "3", path.join(frames, png)], { stdio: "pipe", env: ffEnv() });
    cells.push(`<figure><img src="contact-frames/${png}"><figcaption><b>${mmss(s.from)}–${mmss(s.to)}</b> ${esc(s.id)}<br><span>${esc(s.vo ?? s.bold ?? "")}</span></figcaption></figure>`);
  }
  sections.push(`<section><h2>${cut} s · ${aspect}</h2><div class="grid ${aspect === "9:16" ? "tall" : ""}">${cells.join("")}</div></section>`);
}

const html = `<!doctype html><meta charset="utf-8"><title>The Day Back — contact sheet</title>
<style>
body{font:14px/1.4 Inter,system-ui,sans-serif;margin:32px;background:#f4f6fb;color:#23263b}
h1{font:700 28px Sora,system-ui;margin:0 0 4px} h2{font:600 18px Sora,system-ui;margin:28px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.grid.tall{grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}
figure{margin:0;background:#fff;border:1px solid #d6d6e7;border-radius:10px;overflow:hidden}
img{width:100%;display:block;background:#000} figcaption{padding:8px 10px;font-size:12px} figcaption span{color:#484c7a}
</style>
<h1>Tekly — “The Day Back”</h1><p>One frame per scene, every rendered version. Scratch voice and music; for review before the final voice-over.</p>
${sections.join("\n")}`;
fs.writeFileSync(path.join(outDir, "contact-sheet.html"), html);

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  await p.goto(`file://${path.join(outDir, "contact-sheet.html")}`);
  await p.waitForLoadState("load");
  await p.screenshot({ path: path.join(outDir, "contact-sheet.png"), fullPage: true });
  await b.close();
  console.log("out/contact-sheet.html, out/contact-sheet.png");
})();
