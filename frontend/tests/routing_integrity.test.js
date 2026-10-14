/* QA — routing & linkage integrity.
   Guards against the orphaned-page and broken-nav bugs found in the build-109 QA pass.
   Run:  NODE_PATH=../../node_modules node routing_integrity.test.js  */
const fs = require('fs');
const path = require('path');

const results = [];
function check(name, cond, detail) {
  results.push({ ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

const jsDir = path.join(__dirname, '..', 'static', 'js');
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function allJs() {
  const out = [];
  const walk = (d) => fs.readdirSync(d).forEach((f) => {
    const p = path.join(d, f);
    if (fs.statSync(p).isDirectory()) walk(p);
    else if (f.endsWith('.js')) out.push(fs.readFileSync(p, 'utf8'));
  });
  walk(jsDir);
  return out.join('\n');
}
const js = allJs();

// Aliases that intentionally route elsewhere (label ≠ registered page).
const ALIASES = new Set(['mycompany']);
// Pages reached through the settings rail or company-tabs sub-nav rather than top nav.
const railKeys = [...js.matchAll(/key:\s*'([a-z_-]+)'/g)].map((m) => m[1]);
const companyTabs = [...js.matchAll(/\['([a-z_-]+)'\s*,/g)].map((m) => m[1]);
// Pages reached as sub-tabs of another page (not top-level nav):
//  - Inventory hosts stocktake/transfers/returns/conversions/assets as tabs
const SUBTAB_REACHED = new Set([
  'stocktake', 'transfers', 'returns', 'conversions', 'assets',
  // Suppliers now live under Contacts (Contacts > Suppliers tab); the standalone
  // "suppliers" route redirects there rather than being a top-level nav link.
  'suppliers',
  // Location resources are managed inside each location (Settings > Locations >
  // edit); the standalone "resources" route redirects into Settings.
  'resources',
]);

const registered = new Set([...js.matchAll(/Router\.register\('([a-z_-]+)'/g)].map((m) => m[1]));
const navLinks = new Set([...html.matchAll(/data-page="([a-z_-]+)"/g)].map((m) => m[1]));
const goTargets = new Set([...js.matchAll(/Router\.go\('([a-z_-]+)'/g)].map((m) => m[1]));

console.log('Routing & linkage:');

// 1. Every nav link resolves to a registered page (or is a known alias).
const brokenNav = [...navLinks].filter((p) => !registered.has(p) && !ALIASES.has(p));
check('no nav link points to an unregistered page', brokenNav.length === 0, brokenNav.join(', '));

// 2. Every Router.go target is registered.
const brokenGo = [...goTargets].filter((p) => !registered.has(p));
check('no Router.go target is unregistered', brokenGo.length === 0, brokenGo.join(', '));

// 3. Every registered page is reachable somehow (nav, go, settings rail, company tab, or sub-tab).
const reachable = new Set([...navLinks, ...goTargets, ...railKeys, ...companyTabs, ...SUBTAB_REACHED]);
const orphans = [...registered].filter((p) => !reachable.has(p));
check('no registered page is orphaned (unreachable)', orphans.length === 0,
  'orphaned: ' + orphans.join(', '));

// 4. The inventory-family pages are reached as Inventory sub-tabs, NOT top-level nav.
['assets', 'conversions', 'returns', 'stocktake', 'transfers'].forEach((p) => {
  check(`"${p}" is NOT a top-level nav link (lives under Inventory)`, !navLinks.has(p));
});

// 5. Inbox is fully merged into Chats — no separate inbox page or nav link.
check('inbox is not a top-level nav link', !navLinks.has('inbox'));
check('inbox page no longer registered (merged into Chats)', !registered.has('inbox'));

// 6. Contacts is the top-level nav home; Suppliers now lives UNDER Contacts
//    (Contacts > Suppliers tab), not as its own top-level link.
check('contacts (clients) is in nav', navLinks.has('clients'));
check('suppliers is NOT a top-level nav link (lives under Contacts)', !navLinks.has('suppliers'));
check('suppliers page is still registered/reachable', registered.has('suppliers'));

const ok = results.every((r) => r.ok);
console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
process.exit(ok ? 0 : 1);
