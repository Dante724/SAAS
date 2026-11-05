#!/usr/bin/env node
// Design capture: walks every staff screen (and each of its tabs) of a RUNNING app in light and
// dark, at desktop (1440) and phone (390) widths, and counts the things the design polish fixes:
//
//   contrast    visible, enabled text below WCAG AA against its real background
//               (4.5:1, or 3:1 for large text: 24px+, or 18.66px+ bold)
//   buttons     action buttons that don't look like the shared <Button>: corners other than 12px,
//               a height other than 32 / 36 / 44px, or text lighter than semibold
//   overflow    screens that scroll sideways on a phone (page or main column wider than the screen)
//
// It changes nothing in the app. Before / after numbers in a PR come from two runs of this script.
//
//   node scripts/design-capture/capture.js                  # summary + JSON in design-capture-out/
//   BASE_URL=http://127.0.0.1:8111 node scripts/design-capture/capture.js --only=/orders,/reports
//
// Needs: the app running with the demo seed (SEED_DEMO=true) and Playwright from tests-e2e
// (`cd tests-e2e && npm install`). Sign-in uses the demo admin (E2E_ADMIN_EMAIL / E2E_ADMIN_PASS,
// or the .env.demo the seed writes). See README.md next to this file.
const path = require('path');
const fs = require('fs');
const { createRequire } = require('module');

const ROOT = path.join(__dirname, '..', '..');
const req = createRequire(path.join(ROOT, 'tests-e2e', 'package.json'));
const { chromium } = req('playwright');
const { demoAdminPassword } = require(path.join(ROOT, 'tests-e2e', 'demo-credentials'));

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const EMAIL = process.env.E2E_ADMIN_EMAIL || 'admin@repairos.dev';
const PASS = process.env.E2E_ADMIN_PASS || demoAdminPassword();
const arg = (k) => (process.argv.find((a) => a.startsWith(`--${k}=`)) || '').split('=').slice(1).join('=');
const OUT = arg('out') || path.join(ROOT, 'design-capture-out');
const CONCURRENCY = Number(arg('jobs') || 3);

const HUBS = ['/', '/orders', '/clients', '/assets', '/catalog', '/inventory', '/sales', '/invoices', '/finance', '/cash',
  '/reports', '/marketing', '/tasks', '/my-work', '/timesheets', '/inquiries', '/chats', '/appointments', '/my-earnings'];
const SETTINGS = ['general', 'billing', 'team', 'locations', 'statuses', 'checklists', 'forms', 'industry', 'pricing',
  'finance', 'memberships', 'warehouses', 'directories', 'templates', 'feedback', 'public', 'notifications',
  'integrations', 'sso', 'approvals'].map((s) => `/settings?section=${s}`);
// Drawers worth checking on their own: [screen, what to click to open one].
const DRAWERS = [
  ['/orders', 'main.app-main table tbody tr >> nth=0'],
  ['/inventory', 'main.app-main table tbody tr >> nth=0'],
  ['/clients', 'main.app-main table tbody tr >> nth=0'],
  ['/invoices', 'main.app-main table tbody tr >> nth=0'],
];
const only = arg('only') ? arg('only').split(',') : null;
const screens = [...HUBS, '/settings', ...SETTINGS].filter((s) => !only || only.some((o) => s.startsWith(o)));

const VIEWPORTS = {
  desktop: { viewport: { width: 1440, height: 900 }, isMobile: false, hasTouch: false, deviceScaleFactor: 1 },
  phone: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 },
};

// ---------- runs in the page ----------
function audit() {
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (top, bot) => { const a = top.a + bot.a * (1 - top.a); if (!a) return { r: 0, g: 0, b: 0, a: 0 }; return { r: (top.r * top.a + bot.r * bot.a * (1 - top.a)) / a, g: (top.g * top.a + bot.g * bot.a * (1 - top.a)) / a, b: (top.b * top.a + bot.b * bot.a * (1 - top.a)) / a, a }; };
  const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const cls = (el) => (typeof el.className === 'string' ? el.className : '').replace(/\s+/g, ' ').trim();

  // Background under an element: composite each ancestor's background colour. An image or
  // gradient underneath makes the answer unknowable, so that element is skipped.
  function bgOf(el) {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const c = parse(s.backgroundColor);
      if (c && c.a > 0) { layers.push({ ...c, a: c.a * Number(s.opacity) }); if (c.a >= 1 && Number(s.opacity) >= 1) break; }
    }
    let bg = parse(getComputedStyle(document.body).backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
    if (bg.a < 1) bg = over(bg, { r: 255, g: 255, b: 255, a: 1 });
    for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
    return bg;
  }
  const opacityOf = (el) => { let o = 1; for (let n = el; n && n.nodeType === 1; n = n.parentElement) o *= Number(getComputedStyle(n).opacity); return o; };

  // The app frame: sidebar, the header bar and anything outside the page and its drawers.
  const isFrame = (el) => !!el.closest('main.app-main > header, aside, nav') || !el.closest('main.app-main, [role=dialog]');
  const contrast = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const text = t.nodeValue.replace(/\s+/g, ' ').trim();
    if (!text || !/[\p{L}\p{N}]/u.test(text)) continue;
    const el = t.parentElement;
    if (!el || seen.has(el) || !vis(el)) continue;
    seen.add(el);
    // WCAG exempts inactive controls; greyed-out actions are meant to look inactive.
    if (el.closest(':disabled, [aria-disabled="true"], [inert], .sr-only, [aria-hidden="true"], script, style, noscript')) continue;
    if (el.closest('.material-symbols-outlined, .material-icons, [data-icon]')) continue;   // icon ligatures
    const s = getComputedStyle(el);
    const fg0 = parse(s.color); if (!fg0) continue;
    const bg = bgOf(el); if (!bg) continue;
    const alpha = fg0.a * opacityOf(el);
    if (alpha < 0.05) continue;                          // hidden until hover or focus (a reveal, not a colour)
    const fg = over({ ...fg0, a: alpha }, bg);
    const size = parseFloat(s.fontSize), weight = Number(s.fontWeight) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    const r = ratio(fg, bg);
    if (r + 0.005 < need) contrast.push({ frame: isFrame(el), text: text.slice(0, 50), fg: hex(fg), bg: hex(bg), ratio: Math.round(r * 100) / 100, need, size, cls: cls(el).slice(0, 160) });
  }

  // Buttons that don't look like the shared one.
  const buttons = [];
  const SHARED_ROLES = new Set(['tab', 'switch', 'menuitem', 'menuitemradio', 'menuitemcheckbox', 'option', 'radio', 'checkbox', 'gridcell']);
  for (const el of document.querySelectorAll('button, a[role=button], [role=button]')) {
    if (!vis(el) || SHARED_ROLES.has(el.getAttribute('role') || '') || el.hasAttribute('aria-pressed')) continue;
    if (el.closest('[role=tablist], [role=menu], [role=listbox], nav, [role=grid], .cal, table thead')) continue;
    const s = getComputedStyle(el);
    const label = (el.getAttribute('aria-label') || el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!label) continue;
    const filled = (parse(s.backgroundColor)?.a || 0) > 0 || parseFloat(s.borderTopWidth) > 0;
    if (!filled) continue;                               // text links and bare icons are not "buttons" here
    if (el.matches('.glass-input')) continue;            // a field that opens a picker, not a button
    const box = el.getBoundingClientRect();
    const h = Math.round(box.height);
    if (h > 60) continue;                                // a clickable card or row, not a button
    const radii = [s.borderTopLeftRadius, s.borderTopRightRadius, s.borderBottomRightRadius, s.borderBottomLeftRadius].map(parseFloat);
    if (Math.max(...radii) >= h / 2 - 1) continue;       // a pill or a circle: chips, tags, avatars
    const why = [];
    // 12px corners; one half of a split button keeps 12px on its outer side only
    if (!radii.some((r) => Math.round(r) === 12) || radii.some((r) => r && Math.round(r) !== 12)) why.push(`radius ${s.borderTopLeftRadius}`);
    if (![32, 36, 44].includes(h)) why.push(`height ${h}`);
    if ((Number(s.fontWeight) || 400) < 600) why.push(`weight ${s.fontWeight}`);
    if (why.length) buttons.push({ frame: isFrame(el), label: label.slice(0, 40), why: why.join(', '), cls: cls(el).slice(0, 160) });
  }

  const de = document.scrollingElement || document.documentElement;
  const main = document.querySelector('main.app-main');
  const overflow = de.scrollWidth > de.clientWidth + 1 || (main ? main.scrollWidth > main.clientWidth + 1 : false);
  return { contrast, buttons, overflow, width: de.scrollWidth };
}

// ---------- driver ----------
async function signIn() {
  const r = await fetch(`${BASE}/api/auth/token`, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ username: EMAIL, password: PASS }) });
  if (!r.ok) throw new Error(`sign-in failed: ${r.status}`);
  return (await r.json()).access_token;
}

async function settle(page) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(250);
}

async function captureScreen(browser, token, screen, theme, vpName) {
  const ctx = await browser.newContext({ ...VIEWPORTS[vpName], baseURL: BASE, reducedMotion: 'reduce' });
  await ctx.addInitScript(([t, th]) => { localStorage.setItem('ros_tok', t); localStorage.setItem('ros_theme', th); }, [token, theme]);
  const page = await ctx.newPage();
  const states = [];
  try {
    await page.goto(screen); await settle(page);
    states.push({ state: screen, ...(await page.evaluate(audit)) });
    // Each tab of the first tab bar on the page (the screen's own tabs).
    const tabs = page.locator('main.app-main [role=tablist]').first().locator('[role=tab]');
    const n = Math.min(await tabs.count(), 12);
    for (let i = 1; i < n; i++) {
      const tab = tabs.nth(i);
      if (!(await tab.isVisible().catch(() => false)) || (await tab.isDisabled())) continue;
      const name = (await tab.innerText()).replace(/\s+/g, ' ').trim();
      await tab.click().catch(() => {}); await settle(page);
      states.push({ state: `${screen} › ${name}`, ...(await page.evaluate(audit)) });
    }
    for (const [s, open] of DRAWERS) {
      if (s !== screen) continue;
      await page.goto(screen); await settle(page);
      const row = page.locator(open);
      if (await row.count()) {
        await row.click().catch(() => {}); await settle(page);
        if (await page.locator('[role=dialog]').count()) {
          states.push({ state: `${screen} › drawer`, ...(await page.evaluate(audit)) });
          // and each tab inside the drawer (a job's Parts, Payments, Activity…)
          const dtabs = page.locator('[role=dialog] [role=tablist]').first().locator('[role=tab]');
          const dn = Math.min(await dtabs.count(), 12);
          for (let i = 1; i < dn; i++) {
            const t = dtabs.nth(i);
            if (!(await t.isVisible().catch(() => false)) || (await t.isDisabled())) continue;
            const name = (await t.innerText()).replace(/\s+/g, ' ').trim();
            await t.click().catch(() => {}); await settle(page);
            states.push({ state: `${screen} › drawer › ${name}`, ...(await page.evaluate(audit)) });
          }
        }
      }
    }
  } catch (e) {
    states.push({ state: screen, error: String(e).slice(0, 200), contrast: [], buttons: [], overflow: false });
  }
  await ctx.close();
  return states.map((s) => ({ ...s, theme, viewport: vpName }));
}

(async () => {
  const token = await signIn();
  const browser = await chromium.launch();
  const jobs = [];
  for (const theme of ['light', 'dark']) for (const vp of Object.keys(VIEWPORTS)) for (const s of screens) jobs.push([s, theme, vp]);
  const results = [];
  let next = 0;
  await Promise.all(Array.from({ length: CONCURRENCY }, async () => {
    while (next < jobs.length) {
      const [s, th, vp] = jobs[next++];
      results.push(...(await captureScreen(browser, token, s, th, vp)));
      process.stderr.write('.');
    }
  }));
  await browser.close();
  process.stderr.write('\n');

  // A "spot" is one element on one screen state; the same element failing at both widths counts once,
  // and an element of the app frame (sidebar, header) counts once however many screens show it.
  const spots = (theme, key) => {
    const set = new Map();
    for (const r of results.filter((x) => x.theme === theme)) for (const c of r[key]) {
      const state = c.frame ? '(app frame)' : r.state;
      const k = `${state}|${c.text || c.label}|${c.cls}`;
      if (!set.has(k)) set.set(k, { state, ...c });
    }
    return [...set.values()];
  };
  const summary = {
    base: BASE, when: new Date().toISOString(), states: new Set(results.map((r) => r.state)).size,
    contrastLight: spots('light', 'contrast').length,
    contrastDark: spots('dark', 'contrast').length,
    offDesignButtons: spots('light', 'buttons').length,
    sidewaysScrollPhone: [...new Set(results.filter((r) => r.viewport === 'phone' && r.overflow).map((r) => r.state))],
    errors: results.filter((r) => r.error).map((r) => `${r.state}: ${r.error}`),
  };
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 2));
  fs.writeFileSync(path.join(OUT, 'contrast-light.json'), JSON.stringify(spots('light', 'contrast'), null, 1));
  fs.writeFileSync(path.join(OUT, 'contrast-dark.json'), JSON.stringify(spots('dark', 'contrast'), null, 1));
  fs.writeFileSync(path.join(OUT, 'buttons.json'), JSON.stringify(spots('light', 'buttons'), null, 1));
  console.log(`Screens and tabs checked: ${summary.states}`);
  console.log(`Text below AA contrast, light: ${summary.contrastLight}`);
  console.log(`Text below AA contrast, dark:  ${summary.contrastDark}`);
  console.log(`Off-design buttons:            ${summary.offDesignButtons}`);
  console.log(`Sideways scroll on a phone:    ${summary.sidewaysScrollPhone.length}${summary.sidewaysScrollPhone.length ? ' (' + summary.sidewaysScrollPhone.join('; ') + ')' : ''}`);
  if (summary.errors.length) console.log(`Errors: ${summary.errors.length}\n  ${summary.errors.join('\n  ')}`);
  console.log(`Details: ${path.relative(process.cwd(), OUT) || OUT}/`);
})().catch((e) => { console.error(e); process.exit(1); });
