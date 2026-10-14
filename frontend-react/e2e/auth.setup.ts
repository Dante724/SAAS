import { test as setup, expect } from "@playwright/test";

// Signs in once and saves the session so the real specs start authenticated.
// Creds come from env (never hard-code): E2E_EMAIL / E2E_PASSWORD.
setup("authenticate", async ({ page }) => {
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  if (!email || !password) {
    throw new Error("Set E2E_EMAIL and E2E_PASSWORD to run the E2E suite (see e2e/README.md).");
  }
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /sign in|log in/i }).click();
  await expect(page).not.toHaveURL(/\/login/);   // landed somewhere inside the app
  await page.context().storageState({ path: "e2e/.auth/state.json" });
});
