import { test, expect } from "@playwright/test";

// Responsive layout (launch QA B3): at phone and iPad-portrait widths every top-level page must
// give the main content most of the screen. Before the fix the sidebar never collapsed and took
// ~60% of a 390px phone, wrapping content one word per line; a "sideways scroll" check alone
// missed that because nothing overflowed — it was squeezed. So we measure the main column.

const ROUTES = [
  "/", "/inquiries", "/appointments", "/chats", "/clients", "/orders", "/my-work", "/timesheets",
  "/tasks", "/sales", "/invoices", "/catalog", "/inventory", "/finance", "/marketing", "/reports",
  "/cash", "/assets", "/settings",
];

for (const [label, width, height] of [["phone", 390, 844], ["ipad-portrait", 820, 1180]] as const) {
  test.describe(`${label} ${width}px`, () => {
    test.use({ viewport: { width, height } });

    for (const path of ROUTES) {
      test(`${path}: main content fills the screen`, async ({ page }) => {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        const m = await page.evaluate(() => {
          const main = document.querySelector(".app-main") as HTMLElement | null;
          return {
            main: main ? main.getBoundingClientRect().width : 0,
            vw: window.innerWidth,
            scrollW: document.documentElement.scrollWidth,
          };
        });
        expect(m.main / m.vw, `main column is ${m.main}px of ${m.vw}px on ${path}`).toBeGreaterThanOrEqual(0.7);
        expect(m.scrollW, `page scrolls sideways on ${path}`).toBeLessThanOrEqual(m.vw);
        // The sidebar is behind the menu button at these widths.
        await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
      });
    }

    test("menu button opens the nav drawer and navigates", async ({ page }) => {
      await page.goto("/");
      await page.getByRole("button", { name: "Open menu" }).click();
      const drawer = page.getByRole("dialog", { name: "Main menu" });
      await expect(drawer).toBeVisible();
      await drawer.getByRole("link", { name: "Work orders" }).click();
      await expect(page).toHaveURL(/\/orders/);
      await expect(page.getByRole("dialog", { name: "Main menu" })).toBeHidden();
    });
  });
}
