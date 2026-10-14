// End-to-end for the RepairOS technician PWA: install/manifest, service worker,
// login, and the offline → queue → sync loop. Runs in CI (needs a browser).
const { test, expect } = require('@playwright/test');

const EMAIL = process.env.E2E_EMAIL || 'admin@repairos.dev';
const PASS = process.env.E2E_PASS || 'admin123';

async function login(page) {
  await page.goto('/tech');
  await page.fill('#t-email', EMAIL);
  await page.fill('#t-pass', PASS);
  await page.click('#t-login');
  await expect(page.locator('#nav')).toBeVisible();
}

test('manifest is linked and valid', async ({ page }) => {
  await page.goto('/tech');
  const href = await page.getAttribute('link[rel="manifest"]', 'href');
  expect(href).toBe('/manifest.webmanifest');
  const manifest = await (await page.request.get(href)).json();
  expect(manifest.start_url).toBe('/tech');
  expect(manifest.display).toBe('standalone');
  expect(manifest.icons.some((i) => i.sizes === '512x512')).toBeTruthy();
  expect(manifest.icons.some((i) => i.purpose === 'maskable')).toBeTruthy();
});

test('service worker registers with root scope', async ({ page }) => {
  await page.goto('/tech');
  const scope = await page.evaluate(async () => {
    const reg = await navigator.serviceWorker.ready;
    return reg.scope;
  });
  expect(scope).toMatch(/\/$/);
});

test('technician can sign in and see the dashboard', async ({ page }) => {
  await login(page);
  await expect(page.locator('.stat-grid')).toBeVisible();
  await expect(page.locator('#t-title')).toContainText(/jobs/i);
});

test('assigned jobs open offline, changes queue and sync on reconnect', async ({ page, context }) => {
  await login(page);
  // Prime caches: dashboard + first order (network-first caches the responses).
  const firstJob = page.locator('a.job[href^="#/order/"]').first();
  test.skip((await firstJob.count()) === 0, 'no assigned jobs in the demo seed to exercise offline');
  await firstJob.click();
  await expect(page.locator('#o-status')).toBeVisible();

  // Go offline and confirm the cached order still renders after reload.
  await context.setOffline(true);
  await page.reload();
  await expect(page.locator('#o-status')).toBeVisible();

  // Make a status change while offline -> it must queue (sync banner appears).
  await page.selectOption('#o-status', { index: 1 }).catch(() => {});
  await page.click('#o-save-status');
  await expect(page.locator('.banner.sync')).toContainText(/waiting to sync/i);

  // Back online -> the queue flushes and the banner clears.
  await context.setOffline(false);
  await page.evaluate(() => window.RosPWA && window.RosPWA.flush());
  await expect(page.locator('.banner.sync')).toHaveCount(0, { timeout: 15000 });
});

test('offline navigation falls back to the app shell (not a network error)', async ({ page, context }) => {
  await login(page);
  await context.setOffline(true);
  await page.goto('/tech');
  // Both containers are always in the DOM (one hidden): the shell rendered if either is shown.
  await expect(page.locator('#login:visible, #app:visible')).toBeVisible();
});
