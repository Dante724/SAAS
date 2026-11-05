// Visual regression (design polish D): one screenshot per staff screen at desktop (1440) and phone
// (390) width, compared pixel by pixel with the baseline committed next to this file
// (visual.spec.js-snapshots/). It catches what the tab-shift check can't: a colour, size, spacing
// or font that changed when nothing was meant to.
//
// When a change is MEANT to move pixels, update the baselines on purpose; see README-visual.md.
//
// Keeping a run repeatable:
//   - the browser clock is fixed, so screens that default to "today" (date ranges, calendars)
//     open on the same day every run;
//   - dates, times, running timers and countdowns ("5h left", "2 days ago") are replaced with a
//     fixed placeholder before the shot, since the demo seed creates its records relative to now;
//   - animations and the text caret are off, and each shot waits for the network to go quiet.
const { test, expect } = require('@playwright/test');
const { adminToken } = require('./admin-token');

const FIXED_NOW = new Date('2026-01-14T10:00:00Z');   // a Wednesday, mid-month

// Every staff screen, opened on its first tab.
const SCREENS = [
  ['home', '/'], ['work-orders', '/orders'], ['contacts', '/clients'], ['assets', '/assets'],
  ['catalog', '/catalog'], ['inventory', '/inventory'], ['point-of-sale', '/sales'],
  ['invoices', '/invoices'], ['finance', '/finance'], ['cash', '/cash'], ['reports', '/reports'],
  ['marketing', '/marketing'], ['tasks', '/tasks'], ['my-work', '/my-work'], ['timesheets', '/timesheets'],
  ['inquiries', '/inquiries'], ['chats', '/chats'], ['appointments', '/appointments'],
  ['my-earnings', '/my-earnings'], ['settings', '/settings'], ['settings-team', '/settings?section=team'],
  ['settings-statuses', '/settings?section=statuses'],
];
const VIEWPORTS = [
  { name: 'desktop', viewport: { width: 1440, height: 900 }, isMobile: false, hasTouch: false, deviceScaleFactor: 1 },
  { name: 'phone', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 },
];

let token;
test.beforeAll(async ({ request, baseURL }) => { token = await adminToken(request, baseURL); });

// Runs in the page: put a fixed placeholder over every date, time and running clock.
function steadyText() {
  const MONTH = '(Jan(uary)?|Feb(ruary)?|Mar(ch)?|Apr(il)?|May|June?|July?|Aug(ust)?|Sept?(ember)?|Oct(ober)?|Nov(ember)?|Dec(ember)?)\\.?';
  const patterns = [
    new RegExp(`\\b${MONTH} \\d{1,2}(, \\d{4})?(,? \\d{1,2}:\\d{2}( ?[AP]M)?)?`, 'g'),   // Nov 5, 2026, 10:00 AM
    new RegExp(`\\b\\d{1,2} ${MONTH}( \\d{4})?`, 'g'),                                    // 5 Nov 2026
    /\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b/g,                                               // 11/5/2026, 2026-11-05
    /\b\d{1,2}:\d{2}(:\d{2})?( ?[AP]M)?\b/gi,                                             // 10:00 AM, 01:02:03
    /\b\d{1,2} ?[ap]m\b/gi,                                                              // 9am, 6 PM
    // countdowns and ages: the demo data's real deadlines against the fixed browser clock
    /\b\d+(\.\d+)?\s?(h|hr|hrs|m|min|mins|d|w|mo|y|hours?|minutes?|days?|weeks?|months?|years?)(\s\d+\s?(m|min|mins|h))?(?=\s(left|ago|late|overdue|over|to go))/gi,
    /\bin \d+ (minutes?|hours?|days?|weeks?|months?)\b/gi,
    /\b(Mon(day)?|Tue(s|sday)?|Wed(nesday)?|Thu(rs?|rsday)?|Fri(day)?|Sat(urday)?|Sun(day)?)\b/g,   // weekday names
  ];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t = w.nextNode(); t; t = w.nextNode()) {
    let v = t.nodeValue; const was = v;
    for (const p of patterns) v = v.replace(p, '##');
    if (v !== was) t.nodeValue = v;
  }
  for (const el of document.querySelectorAll('input')) {
    if (/\d{1,2}[/.-]\d{1,2}|\d{4}-\d{2}/.test(el.value)) el.value = '##';
  }
}

for (const vp of VIEWPORTS) {
  for (const [name, path] of SCREENS) {
    test(`looks the same: ${name} @ ${vp.name}`, async ({ browser, baseURL }, info) => {
      test.skip(info.project.name !== 'mobile', 'one run is enough; devices are set per test');
      const { name: _n, ...device } = vp;
      const ctx = await browser.newContext({ ...device, baseURL, reducedMotion: 'reduce', colorScheme: 'light', timezoneId: 'UTC', locale: 'en-US' });
      const page = await ctx.newPage();
      await page.clock.setFixedTime(FIXED_NOW);
      await page.addInitScript((t) => { localStorage.setItem('ros_tok', t); localStorage.setItem('ros_theme', 'light'); }, token);
      await page.goto(path);
      await page.waitForLoadState('networkidle');
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(steadyText);
      await expect(page).toHaveScreenshot(`${name}-${vp.name}.png`, {
        animations: 'disabled', caret: 'hide', fullPage: false,
        // Anti-aliasing differs a little between machines; a real change moves far more than this.
        maxDiffPixels: 0, threshold: 0.2,
      });
      await ctx.close();
    });
  }
}
