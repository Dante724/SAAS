import { test, expect, type Page } from "@playwright/test";

// Orderry parity, team A. Runs against a live app (see README):
//  A1/A2 — editing a service in the drawer keeps every field, and a service hidden from online booking says so in the list.
//  A3    — a product is found by its MPN in Catalog › Products and Inventory › Stock.
// Each test creates its own records through the API (with the signed-in session) and removes them.

const tag = () => Math.random().toString(36).slice(2, 8).toUpperCase();

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const tok = localStorage.getItem("ros_tok") || "";
    const r = await fetch(`/api${path}`, {
      method, credentials: "include",
      headers: { "Content-Type": "application/json", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      body: body == null ? undefined : JSON.stringify(body),
    });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, { method, path, body });
}

for (const [label, width, height] of [["desktop", 1280, 900], ["phone", 375, 812]] as const) {
  test.describe(`catalog parity ${label} ${width}px`, () => {
    test.use({ viewport: { width, height } });

    test("editing a service keeps every field and the booking toggle shows in the list", async ({ page }) => {
      await page.goto("/catalog");
      const t = tag();
      const name = `E2E service ${t}`;
      const me = (await call(page, "GET", "/auth/me")).body;
      const created = await call(page, "POST", "/catalog/services", {
        name, description: "Full teardown", is_labor: true, unit: "h", price: 120, cost_price: 40,
        duration_min: 90, flat_rate: false, warranty_days: 90, barcode: `SVC-${t}`, bookable: true,
        allowed_employee_ids: [me.id], required_resource_types: ["bay"],
      });
      expect(created.status).toBe(201);
      const id = created.body.id;
      try {
        await page.reload();
        await page.getByRole("button", { name: "Services", exact: true }).click();
        const row = page.getByRole("row").filter({ hasText: name });
        await expect(row).toBeVisible();
        await expect(row.getByText("Not bookable online")).toHaveCount(0);   // bookable rows stay quiet

        // Edit only the price and switch off online booking.
        await row.click();
        const price = page.getByLabel(/Labor price/);
        await price.fill("135");
        await page.getByRole("switch", { name: "Show on online booking page" }).uncheck();
        await page.getByRole("button", { name: "Save changes" }).click();
        await expect(page.getByRole("switch", { name: "Show on online booking page" })).toBeHidden();

        const after = (await call(page, "GET", `/catalog/services?search=${encodeURIComponent(name)}`)).body.items[0];
        expect(after).toMatchObject({
          price: 135, bookable: false, warranty_days: 90, barcode: `SVC-${t}`, unit: "h",
          duration_min: 90, description: "Full teardown", is_labor: true,
          allowed_employee_ids: [me.id], required_resource_types: ["bay"],
        });
        await expect(row.getByText("Not bookable online")).toBeVisible();

        // Reopen: the drawer shows what was saved.
        await row.click();
        await expect(page.getByLabel("Warranty (days)")).toHaveValue("90");
        await expect(page.getByLabel("Barcode")).toHaveValue(`SVC-${t}`);
        await expect(page.getByRole("switch", { name: "Show on online booking page" })).not.toBeChecked();
        // no sideways scroll on the drawer at this width
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      } finally {
        await call(page, "DELETE", `/catalog/services/${id}`);
      }
    });

    test("a product is found by its MPN", async ({ page }) => {
      await page.goto("/catalog");
      const t = tag();
      const mpn = `MPN-${t}`;
      const name = `E2E part ${t}`;
      const created = await call(page, "POST", "/catalog/products", { name, sku: `SKU-${t}`, mpn, sell_price: 10 });
      expect(created.status).toBe(201);
      const id = created.body.id;
      try {
        await page.reload();
        await page.getByRole("button", { name: "Products", exact: true }).click();
        await page.getByPlaceholder("Search products, SKU, MPN…").fill(mpn.toLowerCase());
        const row = page.getByRole("row").filter({ hasText: name });
        await expect(row).toHaveCount(1);
        await expect(row.getByText(mpn)).toBeVisible();

        // The drawer shows and keeps it.
        await row.click();
        await expect(page.getByLabel(/^MPN/)).toHaveValue(mpn);
        await page.getByRole("button", { name: "Save changes" }).click();
        const saved = (await call(page, "GET", `/catalog/products/${id}`)).body.product;
        expect(saved.mpn).toBe(mpn);

        // Inventory › Stock search matches it too.
        await page.goto("/inventory");
        await page.getByPlaceholder("Search products, SKU, MPN, barcode…").fill(mpn);
        // It has no stock yet, so the default "In stock" view offers to show it rather than hide it.
        await page.getByRole("button", { name: "Show all matches" }).click();
        await expect(page.getByText(name)).toBeVisible();
        // and the scan lookup (POS, receiving) resolves the exact MPN
        const scan = await call(page, "GET", `/inventory/scan?code=${encodeURIComponent(mpn)}`);
        expect(scan.body.product.id).toBe(id);
      } finally {
        await call(page, "POST", "/catalog/products/bulk", { ids: [id], action: "delete" });
      }
    });
  });
}
