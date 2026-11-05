// Design-audit capture: every route × {1440,390} × {light,dark}; every scroll step; every tab;
// first-row drawer + main-action drawer; metrics + tab-shift deltas. Writes JSONL + JPEGs.
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL || "http://localhost:8111";
const OUT = process.env.OUT;            // design-audit/ folder
const SHOTS = path.join(OUT, "screens");
fs.mkdirSync(SHOTS, { recursive: true });
const TOKEN = fs.readFileSync(process.env.TOKEN_FILE, "utf8").trim();
const ONLY = process.env.ONLY ? new RegExp(process.env.ONLY) : null;
const CFG_ONLY = process.env.CFG || "";
const T = JSON.parse(fs.readFileSync(process.env.TOKENS, "utf8"));
const ADMIN_COOKIES = process.env.ADMIN_COOKIES ? JSON.parse(fs.readFileSync(process.env.ADMIN_COOKIES, "utf8")) : null;

const settingsSections = ["overview", "general", "billing", "team", "locations", "statuses", "checklists", "forms", "industry",
  "pricing", "finance", "memberships", "warehouses", "directories", "templates", "feedback", "public", "notifications",
  "integrations", "ecommerce", "sso", "approvals", "automation", "payroll-rules"];
const appRoutes = ["/", "/orders", "/clients", "/assets", "/inventory", "/catalog", "/sales", "/invoices", "/finance", "/cash",
  "/reports", "/tasks", "/my-work", "/timesheets", "/inquiries", "/chats", "/appointments", "/marketing", "/my-earnings",
  "/this-page-does-not-exist"];
const PAGES = [
  ...appRoutes.map((r) => ({ id: "app" + (r === "/" ? "-home" : r.replace(/\//g, "-")), url: r, kind: "app" })),
  ...settingsSections.map((s) => ({ id: "settings-" + s, url: "/settings?section=" + s, kind: "app", noTabs: false })),
  ...["/login", "/signup", "/reset-password", "/verify-email", "/accept-invite"].map((r) => ({ id: "auth" + r.replace(/\//g, "-"), url: r, kind: "public" })),
  { id: "public-track-lookup", url: "/track", kind: "public" },
  { id: "public-track-job", url: "/track/" + T.track, kind: "public" },
  { id: "public-portal", url: "/portal/" + T.portal, kind: "public" },
  { id: "public-checkin", url: "/c/" + T.checkin, kind: "public" },
  { id: "public-checkin-status-bad", url: "/c/s/not-a-real-token", kind: "public" },
  { id: "public-book", url: "/book/" + T.org, kind: "public" },
  ...["/go/today", "/go/job/" + T.order, "/go/tasks", "/go/inquiries", "/go/new/job", "/go/new/estimate", "/go/scan", "/go/clock", "/go/more"]
    .map((r) => ({ id: "go" + r.replace(/^\/go/, "").replace(/\//g, "-").replace(/-[0-9a-f-]{36}$/, "-detail"), url: r, kind: "go" })),
  { id: "admin-signin", url: "/admin", kind: "public" },
  ...(ADMIN_COOKIES ? [{ id: "admin-home", url: "/admin", kind: "admin" }] : []),
].filter((p) => !ONLY || ONLY.test(p.id));

const CONFIGS = [
  { name: "d-light", w: 1440, h: 900, dark: false },
  { name: "d-dark", w: 1440, h: 900, dark: true },
  { name: "m-light", w: 390, h: 844, dark: false, mobile: true },
  { name: "m-dark", w: 390, h: 844, dark: true, mobile: true },
].filter((c) => !CFG_ONLY || CFG_ONLY.split(",").includes(c.name));

const log = (...a) => { const s = `[${new Date().toISOString().slice(11, 19)}] ` + a.join(" "); console.log(s); fs.appendFileSync(path.join(OUT, "capture.log"), s + "\n"); };
const rec = (o) => fs.appendFileSync(path.join(OUT, "records.jsonl"), JSON.stringify(o) + "\n");
const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "x";

// ---------------- in-page helpers (stringified into the page) ----------------
const PAGE_LIB = `
window.__audit = (() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.05; };
  const root = () => document.querySelector('main.app-main') || document.body;
  const R = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x*10)/10, y: Math.round(r.y*10)/10, w: Math.round(r.width*10)/10, h: Math.round(r.height*10)/10 }; };
  const txt = (el) => (el.innerText || el.getAttribute('aria-label') || el.title || '').replace(/\\s+/g, ' ').trim().slice(0, 60);
  const inChrome = (el) => !!el.closest('aside, nav[aria-label], #nav-drawer, main.app-main > header');
  const dialogs = () => [...document.querySelectorAll('[role=dialog], [aria-modal=true], .shadow-drawer')].filter(vis);
  function scrollableUp(el) { while (el && el !== document.body && el !== document.documentElement) { const cs = getComputedStyle(el); if (/(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 4) return el; el = el.parentElement; } return null; }
  function scroller() {
    const dl = dialogs();
    if (dl.length) { const d = dl[dl.length - 1]; const r = d.getBoundingClientRect(); const pt = document.elementFromPoint(r.x + r.width / 2, r.y + r.height * 0.6); const s = pt && d.contains(pt) ? scrollableUp(pt) : null; if (s) return s;
      const inner = [...d.querySelectorAll('*')].filter((e) => /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 4).sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth)[0]; if (inner) return inner; }
    const m = root(); const mr = m.getBoundingClientRect();
    for (const fy of [0.6, 0.4, 0.8]) { const pt = document.elementFromPoint(mr.x + mr.width * 0.6, mr.y + mr.height * fy); const s = pt ? scrollableUp(pt) : null; if (s && !inChrome(s)) return s; }
    if (document.scrollingElement.scrollHeight > innerHeight + 4) return document.scrollingElement;
    const c = [...m.querySelectorAll('*')].filter((e) => !inChrome(e) && /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 4).sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth)[0];
    return c || document.scrollingElement;
  }
  function scrollInfo() { const s = scroller(); window.__sc = s; return { sh: s.scrollHeight, ch: s.clientHeight, cw: s.clientWidth, ow: s.offsetWidth }; }
  function scrollTo(y) { const s = window.__sc || scroller(); s.scrollTop = y; return s.scrollTop; }
  function h1() { const r = root(); return [...r.querySelectorAll('h1, h2, h3')].find((e) => vis(e) && !inChrome(e) && !e.closest('[role=dialog]')) || null; }
  function pageTitle() { const h = document.querySelector('main.app-main > header'); if (!h) return null; const e = [...h.querySelectorAll('h1,h2,span,div')].find((x) => vis(x) && x.children.length === 0 && (x.innerText || '').trim().length > 1); return e || null; }
  function headerActions() {
    const r = root(); const sc = window.__sc || scroller(); const top = (sc === document.scrollingElement ? 0 : sc.getBoundingClientRect().top);
    return [...r.querySelectorAll('button, a[role=button], a[class*=rounded], input, select')].filter((b) => vis(b) && !inChrome(b) && !b.closest('[role=dialog]') && !b.closest('[data-audit-tg]'))
      .filter((b) => { const q = b.getBoundingClientRect(); return q.top >= top - 4 && q.top < top + 260; })
      .slice(0, 12);
  }
  function tabGroups() {
    const out = []; const seen = new Set();
    const scope = dialogs().length ? dialogs()[dialogs().length - 1] : root();
    const parents = new Set();
    scope.querySelectorAll('button, [role=tab], a').forEach((b) => { if (b.parentElement) parents.add(b.parentElement); });
    for (const p of parents) {
      if (seen.has(p) || inChrome(p) || !vis(p)) continue;
      const kids = [...p.children].filter(vis);
      if (kids.length < 2 || kids.length > 14) continue;
      const btns = kids.filter((k) => k.matches('button, [role=tab], a'));
      if (btns.length !== kids.length) continue;
      if (!btns.every((k) => { const t = txt(k); return t.length > 0 && t.length < 36; })) continue;
      const roleTab = btns.some((k) => k.getAttribute('role') === 'tab' || k.hasAttribute('aria-selected'));
      const underline = btns.filter((k) => /border-b-2/.test(k.className)).length >= 2;
      const pressed = btns.some((k) => k.hasAttribute('aria-pressed') || k.hasAttribute('aria-current'));
      const cls = btns.map((k) => k.className); const counts = {}; cls.forEach((c) => counts[c] = (counts[c] || 0) + 1);
      const oneDiff = Object.keys(counts).length === 2 && Object.values(counts).includes(1) && btns.length >= 3;
      // all on one row
      const tops = btns.map((k) => Math.round(k.getBoundingClientRect().top));
      const oneRow = Math.max(...tops) - Math.min(...tops) < 8;
      if (!(roleTab || underline || (pressed && oneRow) || (oneDiff && oneRow))) continue;
      seen.add(p); out.push(p);
    }
    return out.slice(0, 3).map((p, gi) => { p.setAttribute('data-audit-tg', gi); const b = [...p.children].filter(vis);
      b.forEach((k, ti) => k.setAttribute('data-audit-t', gi + '-' + ti));
      return { gi, rect: R(p), tabs: b.map((k, ti) => ({ ti, label: txt(k), active: k.getAttribute('aria-selected') === 'true' || /font-bold|bg-white shadow|text-primary|border-primary/.test(k.className) })) }; });
  }
  function shiftProbe(gi) {
    const g = document.querySelector('[data-audit-tg="' + gi + '"]');
    const H = h1(); const acts = headerActions();
    const gr = g ? g.getBoundingClientRect() : null;
    let first = null;
    if (gr) first = [...root().querySelectorAll('h2,h3,h4,table,[class*=rounded-2xl],[class*=card],label,p')].find((e) => vis(e) && e.getBoundingClientRect().top > gr.bottom + 2 && !g.contains(e)) || null;
    const sc = window.__sc || scroller();
    const tabs = g ? [...g.children].map(R) : [];
    const PT = pageTitle();
    return { h1: R(H), h1Text: H ? txt(H) : null, title: R(PT), titleText: PT ? txt(PT) : null, actions: acts.map((a) => ({ t: txt(a) || a.getAttribute('placeholder') || a.tagName, ...R(a) })), tabbar: g ? R(g) : null, tabs,
      firstContent: first ? { tag: first.tagName, t: txt(first).slice(0, 30), ...R(first) } : null,
      scW: sc.clientWidth, scH: sc.scrollHeight, docW: document.documentElement.clientWidth };
  }
  function lum(c) { const m = c.match(/[\\d.]+/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map((v) => { v = +v / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; }
  function parse(c) { const m = c.match(/[\\d.]+/g); if (!m) return null; return { r: +m[0], g: +m[1], b: +m[2], a: m[3] === undefined ? 1 : +m[3] }; }
  function bgOf(el) { const stack = []; let e = el; while (e && e.nodeType === 1) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 0.99) break; } e = e.parentElement; }
    let base = document.documentElement.classList.contains('dark') ? { r: 14, g: 17, b: 25 } : { r: 255, g: 255, b: 255 };
    for (let i = stack.length - 1; i >= 0; i--) { const c = stack[i]; base = { r: c.r * c.a + base.r * (1 - c.a), g: c.g * c.a + base.g * (1 - c.a), b: c.b * c.a + base.b * (1 - c.a) }; }
    return 'rgb(' + base.r + ',' + base.g + ',' + base.b + ')'; }
  function metrics(mobile) {
    const scope = dialogs().length ? dialogs()[dialogs().length - 1] : root();
    const all = [...scope.querySelectorAll('button, [role=button], a[href], input[type=checkbox], input[type=radio], select, [role=switch], [role=tab]')].filter(vis);
    const buttons = all.filter((b) => b.matches('button, [role=button]') && !inChrome(b)).map((b) => { const cs = getComputedStyle(b); const r = b.getBoundingClientRect();
      return { t: txt(b).slice(0, 30), w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y), rad: cs.borderTopLeftRadius, fs: cs.fontSize, fw: cs.fontWeight, bg: cs.backgroundColor, fg: cs.color, bd: cs.borderTopWidth + ' ' + cs.borderTopColor, px: cs.paddingLeft, cls: (b.className && b.className.baseVal === undefined ? b.className : '').slice(0, 160) }; });
    const small = mobile ? all.filter((b) => { const r = b.getBoundingClientRect(); return (r.height < 44 || r.width < 44) && r.top < innerHeight * 3; }).map((b) => { const r = b.getBoundingClientRect(); return { t: txt(b).slice(0, 30), w: Math.round(r.width), h: Math.round(r.height), tag: b.tagName }; }) : [];
    const trunc = []; const contrast = []; const texts = [];
    for (const el of scope.querySelectorAll('*')) {
      if (!vis(el)) continue;
      const cs = getComputedStyle(el);
      if (el.children.length === 0 && (el.innerText || '').trim()) {
        if (el.scrollWidth > el.clientWidth + 1 && (cs.textOverflow === 'ellipsis' || cs.overflow === 'hidden' || cs.overflowX === 'hidden')) trunc.push({ t: txt(el).slice(0, 50), w: el.clientWidth, sw: el.scrollWidth });
        if (contrast.length < 60) { const fg = parse(cs.color); if (fg && fg.a > 0.2) { const bg = bgOf(el); const L1 = lum(cs.color), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
          const large = parseFloat(cs.fontSize) >= 24 || (parseFloat(cs.fontSize) >= 18.6 && +cs.fontWeight >= 700);
          if (ratio < (large ? 3 : 4.5)) contrast.push({ t: txt(el).slice(0, 40), ratio: Math.round(ratio * 100) / 100, fg: cs.color, bg, fs: cs.fontSize }); } }
      }
    }
    const H = h1(); window.__sc = scroller();
    const body = (document.body.innerText || '');
    const exTax = (body.match(/[^\\n]{0,30}\\b(ex\\.? ?tax|excl\\.? tax|excluding tax|ex-tax)\\b[^\\n]{0,30}/gi) || []).slice(0, 5);
    const empties = [...scope.querySelectorAll('p, div, td, span')].filter((e) => vis(e) && e.children.length <= 3 && /^(no |nothing |you have no|there are no|empty)/i.test((e.innerText || '').trim()) && (e.innerText || '').length < 140)
      .slice(0, 6).map((e) => { const cs = getComputedStyle(e); const r = e.getBoundingClientRect(); return { t: txt(e).slice(0, 60), fs: cs.fontSize, color: cs.color, align: cs.textAlign, h: Math.round(r.height), hasIcon: !!e.querySelector('svg'), cls: (e.className || '').toString().slice(0, 100) }; });
    const svgs = [...scope.querySelectorAll('svg')].filter(vis); const iconSizes = {}; const strokes = {}; let nonLucide = 0;
    svgs.forEach((s) => { const r = s.getBoundingClientRect(); const k = Math.round(r.width) + 'x' + Math.round(r.height); iconSizes[k] = (iconSizes[k] || 0) + 1; const sw = s.getAttribute('stroke-width') || 'none'; strokes[sw] = (strokes[sw] || 0) + 1; if (!/lucide/.test(s.getAttribute('class') || '')) nonLucide++; });
    const cards = {}; [...scope.querySelectorAll('div, section, article')].filter(vis).forEach((e) => { const r = e.getBoundingClientRect(); if (r.width < 200 || r.height < 60) return; const cs = getComputedStyle(e); if (cs.borderTopWidth !== '0px' && cs.borderTopLeftRadius !== '0px') cards[cs.borderTopLeftRadius] = (cards[cs.borderTopLeftRadius] || 0) + 1; });
    const tables = [...scope.querySelectorAll('table')].filter(vis).map((t) => { const ths = [...t.querySelectorAll('thead th')]; const row = t.querySelector('tbody tr'); const tds = row ? [...row.children] : [];
      const mism = []; ths.forEach((th, i) => { const td = tds[i]; if (!td) return; const a = getComputedStyle(th).textAlign, b = getComputedStyle(td).textAlign; const num = /^[\\s₹$€£-]*[\\d,.]+/.test(td.innerText.trim()); if (a !== b || (num && b !== 'right' && b !== 'end')) mism.push({ col: txt(th), th: a, td: b, num }); });
      return { cols: ths.length, mism }; });
    const fontFam = getComputedStyle(document.body).fontFamily;
    const docOverflow = document.documentElement.scrollWidth - innerWidth;
    return { h1: H ? { t: txt(H), ...R(H), fs: getComputedStyle(H).fontSize, fw: getComputedStyle(H).fontWeight } : null,
      title: (() => { const t = pageTitle(); return t ? { t: txt(t), ...R(t), fs: getComputedStyle(t).fontSize, fw: getComputedStyle(t).fontWeight } : null; })(),
      contentLeft: (() => { const r = root(); const f = [...r.querySelectorAll('h1,h2,p,table,label')].find((e) => vis(e) && !inChrome(e)); return f ? Math.round(f.getBoundingClientRect().x) : null; })(),
      actions: headerActions().map((a) => ({ t: txt(a).slice(0, 30), ...R(a) })), buttons, small, trunc: trunc.slice(0, 30), contrast, exTax, empties, iconSizes, strokes, nonLucide, cards, tables, fontFam, docOverflow, dialogs: dialogs().length };
  }
  function clickFirstRow() {
    const r = root();
    const ok = (e) => vis(e) && !inChrome(e) && e.getBoundingClientRect().top > 60;
    const row = [...r.querySelectorAll('tbody tr')].find(ok) || [...r.querySelectorAll('[role=row]')].find(ok) || [...r.querySelectorAll('li[class*=cursor-pointer], div[class*=cursor-pointer], button[class*=text-left]')].find(ok);
    if (!row) return null; const label = txt(row).slice(0, 40); row.scrollIntoView({ block: 'center' }); row.click(); return label;
  }
  function clickMainAction() { const a = headerActions().find((b) => b.tagName === 'BUTTON' && /new|add|create|\\+/i.test(txt(b) + ' ' + (b.getAttribute('aria-label') || ''))) ; if (!a) return null; a.click(); return txt(a); }
  return { scrollInfo, scrollTo, tabGroups, shiftProbe, metrics, clickFirstRow, clickMainAction, dialogs: () => dialogs().length };
})();`;

async function settle(page, ms = 600) {
  try { await page.waitForLoadState("networkidle", { timeout: 8000 }); } catch { /* polling pages */ }
  await page.waitForTimeout(ms);
}

async function ensureLib(page) { await page.evaluate(PAGE_LIB); }

async function shootScroll(page, base, cfg, maxSteps = 10) {
  await ensureLib(page);
  const info = await page.evaluate(() => window.__audit.scrollInfo());
  const step = Math.max(200, Math.floor(info.ch * 0.9));
  const n = Math.min(maxSteps, Math.max(1, Math.ceil((info.sh - info.ch) / step) + 1));
  const files = [];
  for (let i = 0; i < n; i++) {
    await page.evaluate((y) => window.__audit.scrollTo(y), i * step);
    await page.waitForTimeout(i === 0 ? 150 : 300);
    const f = `${base}__${cfg.name}__s${i}.jpg`;
    await page.screenshot({ path: path.join(SHOTS, f), type: "jpeg", quality: 62 });
    files.push(f);
  }
  await page.evaluate(() => window.__audit.scrollTo(0));
  return { files, scroll: info, truncatedScroll: Math.ceil((info.sh - info.ch) / step) + 1 > maxSteps };
}

async function newCtx(browser, cfg, kind) {
  const ctx = await browser.newContext({
    viewport: { width: cfg.w, height: cfg.h }, deviceScaleFactor: 1, isMobile: !!cfg.mobile, hasTouch: !!cfg.mobile,
    colorScheme: cfg.dark ? "dark" : "light",
    userAgent: cfg.mobile ? "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1" : undefined,
  });
  await ctx.addInitScript(({ tok, dark, kind }) => {
    try {
      if (kind === "app" || kind === "go") localStorage.setItem("ros_tok", tok);
      localStorage.setItem("ros_theme", dark ? "dark" : "light");
      if (dark) localStorage.setItem("go_theme", "dark"); else localStorage.removeItem("go_theme");
      localStorage.setItem("ros_onboarding_done", "1");
    } catch { /* ignore */ }
  }, { tok: TOKEN, dark: cfg.dark, kind });
  if (kind === "admin" && ADMIN_COOKIES) await ctx.addCookies(ADMIN_COOKIES);
  return ctx;
}

async function runPage(browser, p, cfg) {
  const ctx = await newCtx(browser, cfg, p.kind);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
  const base = p.id;
  try {
    // loading state: first paint
    await page.goto(BASE + p.url, { waitUntil: "commit" });
    if (cfg.name === "d-light" || cfg.name === "m-light") {
      await page.waitForTimeout(250);
      const lf = `${base}__${cfg.name}__loading.jpg`;
      await page.screenshot({ path: path.join(SHOTS, lf), type: "jpeg", quality: 55 });
      rec({ page: p.id, url: p.url, cfg: cfg.name, state: "loading", files: [lf] });
    }
    await settle(page, 900);
    await ensureLib(page);
    const shots = await shootScroll(page, base, cfg);
    const m = await page.evaluate((mob) => window.__audit.metrics(mob), !!cfg.mobile);
    rec({ page: p.id, url: p.url, finalUrl: page.url(), cfg: cfg.name, state: "default", ...shots, metrics: m, errors });

    // tabs
    const groups = p.kind === "public" ? await page.evaluate(() => window.__audit.tabGroups()) : await page.evaluate(() => window.__audit.tabGroups());
    for (const g of groups) {
      const before = await page.evaluate((gi) => window.__audit.shiftProbe(gi), g.gi);
      let prev = before;
      for (const tab of g.tabs) {
        const sel = `[data-audit-t="${g.gi}-${tab.ti}"]`;
        if (!(await page.$(sel))) continue;
        try { await page.click(sel, { timeout: 3000 }); } catch { continue; }
        await settle(page, 500);
        await ensureLib(page);
        // re-mark group (React may re-render the bar)
        await page.evaluate(() => window.__audit.tabGroups());
        const after = await page.evaluate((gi) => window.__audit.shiftProbe(gi), g.gi);
        const tb = `${base}__tab${g.gi}-${tab.ti}-${slug(tab.label)}`;
        const s = await shootScroll(page, tb, cfg, 6);
        const tm = await page.evaluate((mob) => window.__audit.metrics(mob), !!cfg.mobile);
        rec({ page: p.id, url: p.url, cfg: cfg.name, state: "tab", group: g.gi, tab: tab.label, tabIndex: tab.ti, first: before, prev, after, ...s, metrics: tm });
        prev = after;
      }
      // back to first tab
      try { await page.click(`[data-audit-t="${g.gi}-0"]`, { timeout: 2000 }); await settle(page, 300); await ensureLib(page); await page.evaluate(() => window.__audit.tabGroups()); } catch { /* ignore */ }
    }

    // drawers: first row + main action
    if (p.kind === "app" || p.kind === "go") {
      for (const which of ["row", "action"]) {
        try {
          await page.goto(BASE + p.url, { waitUntil: "commit" }); await settle(page, 700); await ensureLib(page);
          const label = await page.evaluate((w) => w === "row" ? window.__audit.clickFirstRow() : window.__audit.clickMainAction(), which);
          if (!label) continue;
          await settle(page, 800); await ensureLib(page);
          const urlAfter = page.url();
          const db = `${base}__${which}-${slug(label)}`;
          const s = await shootScroll(page, db, cfg, 8);
          const dm = await page.evaluate((mob) => window.__audit.metrics(mob), !!cfg.mobile);
          // tabs inside the drawer
          const dg = await page.evaluate(() => window.__audit.tabGroups());
          rec({ page: p.id, url: p.url, cfg: cfg.name, state: which, label, urlAfter, ...s, metrics: dm, drawerTabs: dg.map((g) => g.tabs.map((t) => t.label)) });
          for (const g of dg.slice(0, 1)) {
            const before = await page.evaluate((gi) => window.__audit.shiftProbe(gi), g.gi);
            for (const tab of g.tabs) {
              const sel = `[data-audit-t="${g.gi}-${tab.ti}"]`;
              try { await page.click(sel, { timeout: 3000 }); } catch { continue; }
              await settle(page, 500); await ensureLib(page); await page.evaluate(() => window.__audit.tabGroups());
              const after = await page.evaluate((gi) => window.__audit.shiftProbe(gi), g.gi);
              const s2 = await shootScroll(page, `${db}__tab-${tab.ti}-${slug(tab.label)}`, cfg, 6);
              const tm = await page.evaluate((mob) => window.__audit.metrics(mob), !!cfg.mobile);
              rec({ page: p.id, url: p.url, cfg: cfg.name, state: which + "-tab", drawer: label, tab: tab.label, tabIndex: tab.ti, first: before, after, ...s2, metrics: tm });
            }
          }
        } catch (e) { log("drawer err", p.id, cfg.name, which, String(e).slice(0, 120)); }
      }
    }
  } catch (e) {
    log("ERR", p.id, cfg.name, String(e).slice(0, 200));
    rec({ page: p.id, url: p.url, cfg: cfg.name, state: "error", error: String(e).slice(0, 300) });
  } finally { await ctx.close(); }
}

const browser = await chromium.launch({ ignoreDefaultArgs: ["--hide-scrollbars"] });
log("pages", PAGES.length, "configs", CONFIGS.map((c) => c.name).join(","));
await Promise.all(CONFIGS.map(async (cfg) => {
  for (const p of PAGES) {
    if (p.kind === "go" && !cfg.mobile && !/today|detail/.test(p.id)) continue;   // Go is a phone app; check the wide split view only
    const t0 = Date.now();
    await runPage(browser, p, cfg);
    log("done", p.id, cfg.name, ((Date.now() - t0) / 1000).toFixed(1) + "s");
  }
}));
await browser.close();
log("ALL DONE");
