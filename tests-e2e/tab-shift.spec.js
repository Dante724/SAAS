// Tab-shift guard (the UI audit's Phase 3 check): switching a tab must never move the page title,
// the tab bar, the tabs themselves, or the toolbar controls around them. For every tabbed screen
// we click each tab at desktop (1440) and phone (390) widths and compare where those controls sit.
// A control may be disabled on a tab that doesn't use it, but it must not move.
const { test, expect } = require('@playwright/test');
const { adminToken } = require('./admin-token');


const SCREENS = [
  '/orders', '/clients', '/catalog', '/invoices', '/finance', '/reports', '/marketing', '/timesheets',
  '/inquiries', '/appointments', '/my-earnings', '/tasks', '/chats', '/inventory',
  '/settings?section=team', '/settings?section=notifications', '/settings?section=forms',
];
// Desktop is a real desktop browser (mouse, no touch); phone is a touch phone. The CI project
// emulates a phone, so each test opens its own context with the right device.
const VIEWPORTS = [
  { name: 'desktop', viewport: { width: 1440, height: 900 }, isMobile: false, hasTouch: false, deviceScaleFactor: 1 },
  { name: 'phone', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 },
];
const TOLERANCE = 1;   // px

let token;
test.beforeAll(async ({ request, baseURL }) => { token = await adminToken(request, baseURL); });

// Runs in the page: tags every tab bar (role=tablist) and every switch or filter group (a
// role=group of 2+ pressable buttons, the shared Tabs' segmented and chips looks).
function tagGroups() {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const main = document.querySelector('main.app-main') || document.body;
  const groups = [...main.querySelectorAll('[role=tablist], [role=group]')].filter((g) => vis(g) && !g.closest('[role=dialog]'))
    .filter((g) => g.matches('[role=tablist]') || g.querySelectorAll(':scope > [aria-pressed]').length >= 2);
  groups.forEach((g, i) => {
    g.setAttribute('data-ts-group', String(i));
    g.querySelectorAll(':scope > [role=tab], :scope > [aria-pressed]').forEach((t) => t.setAttribute('data-ts-tab', ''));
  });
  return groups.length;
}

// Runs in the page: where the title, one tab group, its tabs and the nearby controls sit.
function measure(groupIndex) {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const main = document.querySelector('main.app-main') || document.body;
  const g = main.querySelector(`[data-ts-group="${groupIndex}"]`);
  if (!g) return null;
  const box = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y) }; };
  const title = document.querySelector('main.app-main > header');
  const gr = g.getBoundingClientRect();
  const label = (el) => (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40);
  // Toolbar controls: anything clickable between the header and just below the tab bar,
  // outside the tab bar itself and outside dialogs.
  const controls = {};
  for (const el of main.querySelectorAll('button, a[href], input, select')) {
    if (!vis(el) || g.contains(el) || el.closest('[role=dialog], aside, nav, main.app-main > header')) continue;
    const r = el.getBoundingClientRect();
    if (r.top > gr.bottom + 72) continue;
    const k = label(el);
    if (k && !(k in controls)) controls[k] = box(el);
  }
  const scrolls = g.scrollWidth > g.clientWidth + 1;     // a long tab strip may scroll sideways
  return {
    title: title ? box(title) : null,
    bar: box(g),
    tabs: scrolls ? [] : [...g.querySelectorAll('[data-ts-tab]')].filter(vis).map(box),
    controls,
  };
}

for (const vp of VIEWPORTS) {
  for (const path of SCREENS) {
    test(`tabs never move the toolbar: ${path} @ ${vp.name}`, async ({ browser, baseURL }, info) => {
      test.skip(info.project.name !== 'mobile', 'one run is enough; devices are set per test');
      const { name: _n, ...device } = vp;
      const ctx = await browser.newContext({ ...device, baseURL });
      const page = await ctx.newPage();
      await page.addInitScript((t) => localStorage.setItem('ros_tok', t), token);
      await page.goto(path);
      await page.waitForLoadState('networkidle');
      const groupCount = await page.evaluate(tagGroups);
      // Every screen listed here has tabs. If none is found, they were hand-built without the shared
      // <Tabs> (no tab roles), and this check could not see them move: that is a failure too.
      expect(groupCount, `${path} has no shared <Tabs>; hand-made tab bars can't be checked`).toBeGreaterThan(0);
      for (let gi = 0; gi < groupCount; gi++) {
        const tabs = page.locator(`main.app-main [data-ts-group="${gi}"] [data-ts-tab]`);
        const n = await tabs.count();
        if (n < 2) continue;
        if (await tabs.first().isDisabled()) continue;   // a whole group greyed out on this view
        await tabs.first().scrollIntoViewIfNeeded();
        await tabs.first().click();
        await page.waitForLoadState('networkidle');
        await page.evaluate(tagGroups);
        const base = await page.evaluate(measure, gi);
        for (let ti = 1; ti < n; ti++) {
          const tab = tabs.nth(ti);
          if (!(await tab.isVisible()) || (await tab.isDisabled())) continue;
          await tab.click();
          await page.waitForLoadState('networkidle');
          await page.evaluate(tagGroups);   // re-tag: a re-render drops the markers
          const now = await page.evaluate(measure, gi);
          if (!base || !now) continue;
          const name = (await tab.innerText()).trim();
          const moved = [];
          const cmp = (what, a, b) => { if (a && b && (Math.abs(a.x - b.x) > TOLERANCE || Math.abs(a.y - b.y) > TOLERANCE)) moved.push(`${what} (${b.x - a.x}, ${b.y - a.y})`); };
          cmp('page title', base.title, now.title);
          cmp('tab bar', base.bar, now.bar);
          base.tabs.forEach((t, i) => cmp(`tab ${i + 1}`, t, now.tabs[i]));
          for (const [k, p] of Object.entries(base.controls)) cmp(`"${k}"`, p, now.controls[k]);
          expect(moved, `switching to "${name}" moved: ${moved.join(', ')}`).toEqual([]);
        }
        // Put the group back on its first tab, so the next group is checked from the default view.
        await page.evaluate(tagGroups);
        await page.locator(`main.app-main [data-ts-group="${gi}"] [data-ts-tab]`).first().click();
        await page.waitForLoadState('networkidle');
      }
      await ctx.close();
    });
  }
}
