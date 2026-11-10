import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(92);
Config.setCodec("h264");
Config.setCrf(18);
Config.setConcurrency(6);

// Use Playwright's headless Chrome when it's installed (Remotion's own download can be blocked).
import fs from "fs";
import os from "os";
import path from "path";
const pw = path.join(os.homedir(), "Library/Caches/ms-playwright");
if (!process.env.REMOTION_OWN_BROWSER && fs.existsSync(pw)) {
  const shell = fs.readdirSync(pw).filter((d) => d.startsWith("chromium_headless_shell-")).sort().reverse()
    .map((d) => path.join(pw, d, "chrome-headless-shell-mac-arm64", "chrome-headless-shell")).find((p) => fs.existsSync(p));
  if (shell) Config.setBrowserExecutable(shell);
}
