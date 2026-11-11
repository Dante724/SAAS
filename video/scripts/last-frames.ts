// Saves each clip's last frame as public/clips/<name>-last.jpg, so a shot that runs past the end
// of its recording holds on the final frame instead of going blank.
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";
import { ffEnv, ffmpegPath } from "./ffmpeg";

const dir = path.join(__dirname, "..", "public", "clips");
for (const f of fs.readdirSync(dir).filter((x) => x.endsWith(".webm"))) {
  const out = path.join(dir, f.replace(/\.webm$/, "-last.jpg"));
  execFileSync(ffmpegPath(), ["-v", "error", "-y", "-sseof", "-0.3", "-i", path.join(dir, f), "-update", "1", "-q:v", "2", out], { env: ffEnv() });
  console.log(path.basename(out));
}
