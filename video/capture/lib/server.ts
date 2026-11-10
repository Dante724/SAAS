// Start a fresh Tekly app server (own SQLite DB in a temp dir) for the capture, and stop it after.
import { spawn, spawnSync, type ChildProcess } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import net from "net";

export const REPO = path.resolve(__dirname, "../../..");
const PY = process.env.TEKLY_PYTHON || "/Users/dan/Desktop/repairos/.venv/bin/python";

function newest(dir: string): number {
  let t = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) t = Math.max(t, newest(p));
    else t = Math.max(t, fs.statSync(p).mtimeMs);
  }
  return t;
}

/** The server serves frontend-react/dist; rebuild it when the sources are newer than the build. */
export function ensureFrontendBuilt() {
  const fe = path.join(REPO, "frontend-react");
  const idx = path.join(fe, "dist", "index.html");
  const stale = !fs.existsSync(idx) || newest(path.join(fe, "src")) > fs.statSync(idx).mtimeMs;
  if (!stale) return;
  console.log("[server] building frontend-react (dist is stale)…");
  const r = spawnSync("npx", ["vite", "build"], { cwd: fe, stdio: "inherit" });
  if (r.status !== 0) throw new Error("frontend build failed");
}

async function portFree(port: number) {
  return new Promise<boolean>((res) => {
    const s = net.createServer().once("error", () => res(false)).once("listening", () => s.close(() => res(true)));
    s.listen(port, "127.0.0.1");
  });
}

export type Server = { base: string; stop: () => Promise<void>; dir: string };

export async function startServer(preferredPort = 8131): Promise<Server> {
  let port = preferredPort;
  while (!(await portFree(port))) port++;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "tekly-capture-"));
  const log = fs.openSync(path.join(dir, "server.log"), "w");
  const env = {
    ...process.env,
    ENVIRONMENT: "development",
    DATABASE_URL: `sqlite+aiosqlite:///${path.join(dir, "capture.db")}`,
    RATE_LIMIT_IP_PER_MIN: "100000",
    RATE_LIMIT_USER_PER_MIN: "100000",
    SEED_DEMO: "false",
  };
  const child: ChildProcess = spawn(PY, ["-m", "uvicorn", "main:app", "--port", String(port)], {
    cwd: REPO, env, stdio: ["ignore", log, log],
  });
  const base = `http://localhost:${port}`;
  const t0 = Date.now();
  for (;;) {
    if (child.exitCode != null) throw new Error(`server exited early, see ${dir}/server.log`);
    try { if ((await fetch(base + "/api/health")).ok) break; } catch { /* not up yet */ }
    if (Date.now() - t0 > 90_000) throw new Error(`server did not start, see ${dir}/server.log`);
    await new Promise((r) => setTimeout(r, 500));
  }
  console.log(`[server] up at ${base} (db + log in ${dir})`);
  return {
    base, dir,
    stop: async () => {
      if (child.exitCode == null) {
        child.kill("SIGTERM");
        await new Promise((r) => { const t = setTimeout(() => { child.kill("SIGKILL"); r(null); }, 5000); child.once("exit", () => { clearTimeout(t); r(null); }); });
      }
    },
  };
}
