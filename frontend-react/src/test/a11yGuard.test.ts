import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

// Source-level guard (29 Oct 2026 audit): a clickable div/tr/td/span must be reachable by keyboard
// — use a <button>, or spread clickable()/clickableRow() from lib/clickable. Backdrops that only
// close a drawer on outside click are aria-hidden (the drawer has its own close button); modal
// wrappers that close on outside click are role="presentation".

const SRC = join(__dirname, "..");
const TAG = /<(div|td|tr|span|li|section|article|p|img)\b((?:[^<>{}]|\{(?:[^{}]|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})*\})*?)\/?>/gs;

function files(dir: string, exts = [".tsx"]): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return files(p, exts);
    return exts.some((e) => p.endsWith(e)) && !p.includes(".test.") ? [p] : [];
  });
}

function scan() {
  const clicks: string[] = [];
  const imgs: string[] = [];
  for (const f of files(SRC)) {
    // Whole-line comments mention tags in prose ("a plain <img src> can't send the header").
    const s = readFileSync(f, "utf8").replace(/^\s*(\/\/|\*|\/\*).*$/gm, "");
    for (const m of s.matchAll(TAG)) {
      const [, tag, attrs] = m;
      const where = `${f.replace(SRC, "src")}:${s.slice(0, m.index).split("\n").length}`;
      if (tag === "img") { if (!/\balt=/.test(attrs)) imgs.push(where); continue; }
      const oc = /\bonClick=\{([^}]*)/.exec(attrs);
      if (!oc || /\b(role|onKeyDown|aria-hidden)=/.test(attrs)) continue;
      if (oc[1].includes("stopPropagation") && oc[1].length < 40) continue; // keeps a row's click off its controls
      clicks.push(where);
    }
  }
  return { clicks, imgs };
}

describe("keyboard and screen-reader basics", () => {
  const { clicks, imgs } = scan();
  it("every clickable non-button element is keyboard reachable", () => {
    expect(clicks).toEqual([]);
  });
  it("every <img> has alt text (alt=\"\" for decoration)", () => {
    expect(imgs).toEqual([]);
  });
  it("nothing uses the removed Material Symbols web font", () => {
    const users = files(SRC).filter((f) => readFileSync(f, "utf8").includes("material-symbols-outlined"));
    expect(users).toEqual([]);
  });
  it("no load or save swallows its error silently", () => {
    // Use loadFailed()/saveFailed() from lib/notice; a truly harmless one says why: .catch(() => { /* silent: … */ })
    const bare = files(SRC, [".ts", ".tsx"]).filter((f) =>
      readFileSync(f, "utf8").split("\n").some((l) => l.includes(".catch(() => {})") && !/^\s*\/\//.test(l)));
    expect(bare).toEqual([]);
  });
});
