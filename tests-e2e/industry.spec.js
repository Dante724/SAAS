// @ts-check
/*
 * End-to-end: create work order + add asset, template-aware.
 *
 * Runs in a real browser against a running Tekly instance (that's why it lives
 * in tests-e2e/ and can't run in the sandbox). Assumes the dev server on
 * http://127.0.0.1:8000 seeded with admin@repairos.dev (password: DEMO_ADMIN_PASSWORD or .env.demo).
 *
 *   npx playwright test industry.spec.js
 *
 * Covers, for two industries (mobile_phone → "+ Ticket" / IMEI, and auto →
 * "+ Order" / VIN): the create button label follows the template, the create
 * modal renders the template's group + identifier, a work order can be created,
 * and an asset can be added to a client with the template-driven fields.
 */
const { test, expect } = require('@playwright/test');
const { demoAdminPassword } = require('./demo-credentials');

const BASE = process.env.TEKLY_BASE || process.env.REPAIROS_BASE || 'http://127.0.0.1:8000';
const EMAIL = 'admin@repairos.dev';
const PASSWORD = demoAdminPassword();

/** Set the org's industry template through the API, using the app's stored token. */
async function setTemplate(page, key) {
  const res = await page.evaluate(async (tk) => {
    const token = localStorage.getItem('token') || localStorage.getItem('access_token');
    const r = await fetch('/api/settings/industry-template', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ template_key: tk }),
    });
    return r.status;
  }, key);
  expect(res).toBeLessThan(300);
}

async function login(page) {
  await page.goto(BASE);
  // login form
  await page.fill('input[type="email"], #login-email', EMAIL).catch(() => {});
  await page.fill('input[type="password"], #login-password', PASSWORD).catch(() => {});
  await page.click('button:has-text("Sign in"), button:has-text("Log in"), button[type="submit"]').catch(() => {});
  await page.waitForLoadState('networkidle');
}

test.describe('industry-template work-order + asset flow', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('phone template → "+ Ticket" button, IMEI identifier, create WO', async ({ page }) => {
    await setTemplate(page, 'mobile_phone');
    await page.goto(`${BASE}/#orders`);
    await page.waitForLoadState('networkidle');

    // create button reads "+ Ticket"
    const createBtn = page.locator('button', { hasText: /\+ Ticket/ }).first();
    await expect(createBtn).toBeVisible();
    await createBtn.click();

    // modal shows the Group select + IMEI identifier label
    await expect(page.locator('#no-asset-group')).toBeVisible();
    await expect(page.locator('#no-ident-label')).toHaveText(/IMEI/);

    // fill minimal fields and submit
    await page.fill('#no-asset-brand', 'Apple');
    await page.fill('#no-asset-model', 'iPhone 14');
    await page.fill('#no-asset-serial', '355823001234567');
    await page.click('button:has-text("Create ticket"), button:has-text("Create order")');
    await expect(page.locator('.toast, [class*="toast"]')).toContainText(/creat|added|success/i);
  });

  test('auto template → "+ Order" button, VIN identifier', async ({ page }) => {
    await setTemplate(page, 'auto');
    await page.goto(`${BASE}/#orders`);
    await page.waitForLoadState('networkidle');

    const createBtn = page.locator('button', { hasText: /\+ Order/ }).first();
    await expect(createBtn).toBeVisible();
    await createBtn.click();

    await expect(page.locator('#no-asset-group')).toBeVisible();
    await expect(page.locator('#no-ident-label')).toHaveText(/VIN/);
    // group options are the auto template's
    const groupText = await page.locator('#no-asset-group').innerText();
    expect(groupText).toMatch(/Car|Truck/);
  });

  test('add asset to a client uses the template group + identifier', async ({ page }) => {
    await setTemplate(page, 'auto');
    await page.goto(`${BASE}/#clients`);
    await page.waitForLoadState('networkidle');

    // open the first client, then Assets tab → Add
    await page.locator('.client-row, [class*="client"]').first().click().catch(() => {});
    await page.click('button:has-text("Assets"), [role="tab"]:has-text("Assets")').catch(() => {});
    await page.click('button:has-text("Add"), button:has-text("+ Add")').catch(() => {});

    // template-driven asset modal
    await expect(page.locator('#dv-group')).toBeVisible();
    await expect(page.locator('#dv-ident-label')).toHaveText(/VIN/);
    await page.fill('#dv-brand', 'Toyota');
    await page.fill('#dv-model', 'Corolla');
    await page.fill('#dv-ident', '1HGCM82633A004352');
    await page.click('button:has-text("Add vehicle"), button:has-text("Add device"), button:has-text("Add asset")');
    await expect(page.locator('.toast, [class*="toast"]')).toContainText(/add|success/i);
  });
});
