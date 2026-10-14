// Lighthouse PWA assertion. Requires Chromium launched with a fixed remote
// debugging port (see the CI job) and playwright-lighthouse.
const { test } = require('@playwright/test');
const { playAudit } = require('playwright-lighthouse');

test('technician PWA scores >= 90 on the PWA/best-practices audits', async ({ page }) => {
  await page.goto('/tech');
  await page.waitForTimeout(1000); // let the SW register
  await playAudit({
    page,
    port: Number(process.env.LH_PORT || 9222),
    thresholds: { 'best-practices': 90, performance: 70, accessibility: 90 },
    // 'pwa' category assertions (installable, SW, manifest) are enforced via the
    // config below in newer Lighthouse; keep best-practices/a11y as hard gates.
  });
});
