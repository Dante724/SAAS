#!/usr/bin/env node
// UI-text content lint: catch raw snake_case keys leaking into user-facing labels
// (e.g. a status pill rendering "waiting_parts" instead of "Waiting parts").
//
// It flags ONLY *literal JSX text* that is a snake_case identifier — text sitting directly
// between tags like `>waiting_parts<`. It deliberately ignores:
//   - JSX expressions `>{foo}<` (dynamic — can't tell statically, and usually humanized)
//   - attributes (className, data-*, keys, style), imports, comments
//   - a small allowlist of legitimate literal tokens (units, code samples)
// so it has near-zero false positives while still catching the real "key as label" mistake.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, extname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "src");

// literal text node with a snake_case word: `>  something_like_this  <`
// (letters/underscores/spaces only between the tags, and at least one a_b word)
const JSX_TEXT = />\s*([A-Za-z][A-Za-z ]*?[a-z]+_[a-z]+[A-Za-z ]*?)\s*</g;

// legitimate literal snake_case that is genuinely the right text to show
const ALLOW = new Set([]);

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const s = statSync(p);
    if (s.isDirectory()) out.push(...walk(p));
    else if ([".tsx", ".ts"].includes(extname(p)) && !p.endsWith(".test.ts") && !p.endsWith(".test.tsx")) out.push(p);
  }
  return out;
}

const hits = [];
for (const file of walk(ROOT)) {
  const text = readFileSync(file, "utf8");
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    // skip obvious non-label contexts
    if (/\b(className|data-[a-z]+|aria-|style|import |from ["']|key=)/.test(line)) return;
    let m;
    JSX_TEXT.lastIndex = 0;
    while ((m = JSX_TEXT.exec(line))) {
      const word = m[1].trim();
      if (ALLOW.has(word)) continue;
      hits.push(`${file.replace(ROOT, "src")}:${i + 1}  →  "${word}"`);
    }
  });
}

if (hits.length) {
  console.error(`\n✖ UI-text lint: ${hits.length} raw snake_case string(s) rendered as label text:\n`);
  for (const h of hits) console.error("  " + h);
  console.error("\nRender a human label (use the record's .name, or humanize()), not the raw key.\n");
  process.exit(1);
}
console.log("✓ UI-text lint: no raw snake_case leaking into labels.");
