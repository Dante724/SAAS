import { test, expect, type Page } from "@playwright/test";

// D2 end to end on the simulated provider: turn on "Create contacts from calls" with a tag,
// simulate an inbound call from an unknown number, then find the new contact in Contacts by
// that tag, marked "Created from call". Needs a signed-in admin (see e2e/README.md).

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ([m, p, b]) => {
    const t = localStorage.getItem("ros_tok") || localStorage.getItem("ros_token") || localStorage.getItem("token") || "";
    const r = await fetch("/api" + p, { method: m as string, credentials: "include",
      headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
      body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [method, path, body] as const);
}

test("a simulated inbound call creates a tagged contact", async ({ page }) => {
  const stamp = Date.now().toString().slice(-7);
  const tagName = `Caller ${stamp}`;
  const number = `+1415${stamp}`;

  await page.goto("/settings");
  const tag = await api(page, "POST", "/tags", { name: tagName, color: "#2563eb" });
  expect(tag.status).toBe(201);
  // demo-safe provider; leaves any Twilio account untouched
  expect((await api(page, "PUT", "/telephony/config", { provider: "simulated", enabled: true })).status).toBe(200);

  await page.reload();
  await page.getByRole("button", { name: "Integrations" }).first().click();
  const card = page.locator("section", { has: page.getByRole("heading", { name: "Calls & telephony" }) });
  await expect(card.getByTestId("tel-badge")).toHaveText("Simulated");

  const auto = card.getByLabel("Create contacts from calls");
  if (!(await auto.isChecked())) await auto.check();
  await card.getByLabel("Which calls").selectOption("incoming");
  const chip = card.getByRole("button", { name: tagName });
  if ((await chip.getAttribute("aria-pressed")) !== "true") await chip.click();
  await card.getByRole("button", { name: "Save automation" }).click();
  await expect(card.getByRole("status")).toHaveText("Saved.");

  await card.getByLabel("Caller's number").fill(number);
  await card.getByRole("button", { name: /Simulate call/ }).click();
  await expect(card.getByRole("status")).toContainText("new contact was created");

  // the contact is in Contacts, found by the tag filter, and says where it came from
  await page.goto("/clients");
  await page.getByLabel("Filter by tag").selectOption({ label: tagName });
  await expect(page.getByText(`Tag: ${tagName}`)).toBeVisible();
  const row = page.getByRole("row", { name: new RegExp(stamp.slice(-4)) });
  await expect(row).toHaveCount(1);
  await row.click();
  await expect(page.getByText("Created from call")).toBeVisible();
});
