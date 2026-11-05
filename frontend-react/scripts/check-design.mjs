#!/usr/bin/env node
// Design drift check (the UI audit's Phase 3 guard). It counts, per file, the patterns the shared
// components replaced, and fails when any file has MORE than the recorded baseline, so the app
// can only get more consistent. The baseline only goes down: after migrating a screen, run
// `npm run lint:design -- --update` to lock in the lower counts.
//
// Rules (outside src/components, which is where the shared looks live):
//   button-look   a <button> styled by hand as a filled main/danger button → use <Button>
//   navy-button   a <button> or link with the dark navy fill → main buttons are sapphire
//   text-px       an arbitrary text size like text-[11px] → use text-2xs / xs / sm / title …
//   raw-hex       a hard-coded colour in a class (bg-[#…], text-[#…]) → use the theme colours
//   gradient      a gradient fill (bg-gradient-to-*, linear-gradient) → flat surfaces only
//   blur          backdrop-blur on anything → solid surfaces (scrims are plain colour)
//   bold-tab      a selected tab that turns bold → selected tabs change colour only
import { readFileSync, readdirSync, statSync, writeFileSync, existsSync } from "node:fs";
import { join, extname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const ROOT = join(HERE, "..", "src");
const BASELINE = join(HERE, "design-baseline.json");
const UPDATE = process.argv.includes("--update");

const RULES = {
  "button-look": /<button\b[^>]*className=[^>]*\b(bg-primary|bg-rose-600|bg-emerald-600)\b(?![/-])/g,
  "navy-button": /<(button|a|Link)\b[^>]*className=[^>]*\bbg-ink-primary\b(?![/-])/g,
  "text-px": /\btext-\[\d+(\.\d+)?px\]/g,
  "raw-hex": /\b(bg|text|border|ring|fill|stroke|from|to|via)-\[#[0-9a-fA-F]{3,8}\]/g,
  gradient: /\bbg-gradient-to-[a-z]+\b|linear-gradient\(/g,
  blur: /\bbackdrop-blur(-[a-z0-9[\]px]+)?\b/g,
  "bold-tab": /(font-bold\s+border-primary|border-primary\s+font-bold|text-primary\s+font-bold\s+border)/g,
};

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (extname(p) === ".tsx" && !p.includes(".test.")) out.push(p);
  }
  return out;
}

const counts = {};
for (const file of walk(ROOT)) {
  const rel = relative(ROOT, file);
  if (rel.startsWith("components/")) continue;          // the shared looks are defined here
  // Brand art (the hex-nut mark) is the one place a gradient is allowed.
  const src = readFileSync(file, "utf8");
  for (const [rule, re] of Object.entries(RULES)) {
    if (rule === "gradient" && rel.includes("brand")) continue;
    const n = (src.match(re) || []).length;
    if (n) (counts[rel] ||= {})[rule] = n;
  }
}

const base = existsSync(BASELINE) ? JSON.parse(readFileSync(BASELINE, "utf8")) : {};
if (UPDATE) {
  // Never let --update raise a count that was lower: a regression must be fixed, not recorded.
  const next = {};
  for (const [file, rules] of Object.entries(counts)) for (const [rule, n] of Object.entries(rules)) {
    const was = base[file]?.[rule];
    if (was !== undefined && n > was) { console.error(`✗ ${file}: ${rule} went up (${was} → ${n}); fix it instead of recording it.`); process.exit(1); }
    (next[file] ||= {})[rule] = n;
  }
  const sorted = Object.fromEntries(Object.keys(next).sort().map((k) => [k, next[k]]));
  writeFileSync(BASELINE, JSON.stringify(sorted, null, 2) + "\n");
  const total = Object.values(next).reduce((s, r) => s + Object.values(r).reduce((a, b) => a + b, 0), 0);
  console.log(`✓ Design baseline updated: ${total} legacy patterns left across ${Object.keys(next).length} files.`);
  process.exit(0);
}

const worse = [];
let total = 0, baseTotal = 0;
for (const [file, rules] of Object.entries(counts)) for (const [rule, n] of Object.entries(rules)) {
  total += n;
  const was = base[file]?.[rule] ?? 0;
  if (n > was) worse.push(`  ${file}: ${rule} ${was} → ${n}`);
}
for (const rules of Object.values(base)) for (const n of Object.values(rules)) baseTotal += n;

if (worse.length) {
  console.error("✗ Design drift: these files gained hand-made looks the shared components replace.\n" + worse.join("\n") +
    "\nUse <Button>, <Tabs>, theme colours and named text sizes (see src/components). Rules are explained at the top of scripts/check-design.mjs.");
  process.exit(1);
}
const gone = baseTotal - total;
console.log(`✓ Design drift: no file got worse (${total} legacy patterns left${gone > 0 ? `; ${gone} fewer than the baseline, run with --update to lock that in` : ""}).`);
