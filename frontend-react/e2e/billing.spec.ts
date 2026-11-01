import { test, expect, type Page } from "@playwright/test";

// Billing flow (team B): partial payment → overpayment kept as client balance → credit note
// against the invoice → print it. Seeds its own client, till and invoice through the API with the
// signed-in session, then drives everything else through the UI.

async function apiCall<T>(page: Page, method: string, path: string, body?: unknown): Promise<T> {
  return page.evaluate(async ({ method, path, body }) => {
    const tok = localStorage.getItem("ros_tok") || localStorage.getItem("ros_token") || localStorage.getItem("token");
    const r = await fetch(`/api${path}`, {
      method, credentials: "include",
      headers: { "Content-Type": "application/json", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${await r.text()}`);
    return r.json();
  }, { method, path, body }) as Promise<T>;
}

test("partial pay → overpay → credit note → print", async ({ page }) => {
  await page.goto("/invoices");
  const tag = Date.now().toString(36);
  const client = await apiCall<{ id: string }>(page, "POST", "/clients", { name: `E2E Billing ${tag}` });
  await apiCall(page, "POST", "/cash/accounts", { name: `E2E till ${tag}`, payment_option: "cash" });
  const inv = await apiCall<{ id: string; invoice_number: string }>(page, "POST", "/finance/invoices", {
    client_id: client.id, lines: [{ description: "Screen replacement", qty: 1, unit_price: 100 }] });

  await page.reload();
  await page.getByRole("button", { name: inv.invoice_number, exact: true }).click();
  const drawer = page.getByRole("dialog", { name: `Invoice ${inv.invoice_number}` });
  const balance = drawer.getByRole("region", { name: "Balance due" });
  await expect(balance).toContainText("100.00");

  // 1) partial payment
  await drawer.getByRole("button", { name: "Record payment" }).click();
  let pay = page.getByRole("dialog", { name: "Record payment" });
  await pay.getByLabel("Amount").fill("40");
  await pay.getByLabel("Account").selectOption({ label: `E2E till ${tag}` });
  await pay.getByRole("button", { name: /^Record / }).click();
  await expect(pay).toBeHidden();
  await expect(balance).toContainText("60.00");
  await expect(drawer).toContainText("Partial");

  // 2) overpay: 75 against 60 due, keep the 15 as client balance
  await drawer.getByRole("button", { name: "Record payment" }).click();
  pay = page.getByRole("dialog", { name: "Record payment" });
  await pay.getByLabel("Amount").fill("75");
  await expect(pay).toContainText("15.00 more than the balance due");
  await pay.getByRole("radio", { name: /Keep as client balance/ }).check();
  await pay.getByRole("button", { name: /^Record / }).click();
  await expect(pay).toBeHidden();
  await expect(balance).toContainText("0.00");
  await expect(drawer).toContainText("Paid");
  const bal = await apiCall<{ balance: number }>(page, "GET", `/clients/${client.id}/balance`);
  expect(bal.balance).toBe(-115);            // 100 paid + 15 kept as credit

  // 3) credit note against the invoice for 20
  await drawer.getByRole("button", { name: "Create credit note" }).click();
  const note = page.getByRole("dialog", { name: "New credit note" });
  await note.getByLabel("Line 1 unit price").fill("20");
  await note.getByLabel("Reason").fill("Goodwill for the delay");
  await note.getByRole("button", { name: /^Issue / }).click();
  await page.getByRole("button", { name: "Issue", exact: true }).click();   // confirm
  const issued = page.getByRole("dialog", { name: /^Credit note CN-/ });
  await expect(issued).toContainText("Issued");
  await expect(balance.getByText(/Credit note/)).toBeVisible();
  await expect(balance).toContainText("20.00");

  // 4) print: the PDF comes back from /print/credit-notes/{id}
  await issued.getByRole("button", { name: "More actions" }).click();   // PDF / Print / Cancel live in "⋮"
  const [pdf] = await Promise.all([
    page.waitForResponse((r) => /\/api\/print\/credit-notes\//.test(r.url())),
    page.getByRole("menuitem", { name: "Download PDF" }).click(),
  ]);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");

  // the client's money tab lists the credit note
  const list = await apiCall<{ items: { number: string; status: string }[] }>(page, "GET", `/finance/credit-notes?client_id=${client.id}`);
  expect(list.items[0].status).toBe("issued");
});
