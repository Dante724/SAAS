import { test, expect, type Page } from "@playwright/test";

// F1 + F2: scan a barcode into the POS cart with a keyboard-wedge burst, then take the payment on
// a simulated card reader and approve the test card. Needs a local (non-production) server, since
// the simulated reader only exists there. Creates its own product and reader and removes the reader.

async function api<T = any>(page: Page, method: string, path: string, body?: unknown): Promise<T> {
  return page.evaluate(async ({ method, path, body }) => {
    const tok = localStorage.getItem("ros_tok") || "";
    const r = await fetch("/api" + path, {
      method, credentials: "include",
      headers: { "Content-Type": "application/json", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      body: body == null ? undefined : JSON.stringify(body),
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${text}`);
    return text ? JSON.parse(text) : null;
  }, { method, path, body });
}

test("scan a barcode into the cart and charge it on a simulated reader", async ({ page }) => {
  await page.goto("/sales");
  const barcode = `E2E${Date.now()}`;
  const name = `E2E cable ${barcode.slice(-5)}`;
  const product = await api(page, "POST", "/catalog/products", { name, sell_price: 12, barcode });
  const loc = (await api(page, "GET", "/staff/locations")).items[0].id;
  await api(page, "POST", `/inventory/parts/${product.id}/adjust`, { location_id: loc, qty: 5, movement_type: "receive" });
  const reader = await api(page, "POST", "/payments/terminal/readers", { provider: "simulated", label: "E2E reader" });

  try {
    await page.reload();
    await page.locator("body").click({ position: { x: 5, y: 5 } });     // focus outside any field
    await page.keyboard.type(barcode, { delay: 5 });                       // a wedge scanner types fast…
    await page.keyboard.press("Enter");                                    // …and ends with Enter
    const line = page.getByTestId("cart-line").filter({ hasText: name });
    await expect(line).toHaveCount(1);

    await page.getByRole("button", { name: "Charge on reader" }).click();
    const dlg = page.getByRole("dialog", { name: "Charge on reader" });
    await dlg.getByLabel("Card reader").selectOption(reader.id);
    await dlg.getByRole("button", { name: /to reader/ }).click();
    await expect(dlg.getByText("Waiting for card…")).toBeVisible();
    await expect(dlg.getByText("Test mode")).toBeVisible();
    await dlg.getByRole("button", { name: "Approve" }).click();
    await expect(dlg.getByText("Approved")).toBeVisible();
    await dlg.getByRole("button", { name: "Done" }).click();

    const receipt = page.getByRole("dialog", { name: "Receipt" });
    await expect(receipt.getByText("Sale complete")).toBeVisible();
    const sales = (await api(page, "GET", "/sales")).items;
    expect(sales[0].paid).toBe(true);
    expect(sales[0].total).toBe(12);
  } finally {
    await api(page, "DELETE", `/payments/terminal/readers/${reader.id}`).catch(() => {});
  }
});
