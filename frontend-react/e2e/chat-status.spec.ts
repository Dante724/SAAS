import { test, expect, type Page } from "@playwright/test";

// Chat status flow (team C, C3): a new conversation shows under New, a reply moves it to
// Replied, the customer writing back moves it to Reply needed, it can be completed by hand,
// and it can be moved to Spam and restored. Runs against a live app like the rest of e2e/.

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ([m, p, b]) => {
    const tok = localStorage.getItem("ros_tok") || localStorage.getItem("ros_token") || localStorage.getItem("token") || "";
    const r = await fetch(`/api${p}`, {
      method: m as string, credentials: "include",
      headers: { "Content-Type": "application/json", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      body: b ? JSON.stringify(b) : undefined,
    });
    if (!r.ok) throw new Error(`${m} ${p} → ${r.status} ${await r.text()}`);
    return r.json();
  }, [method, path, body] as const);
}

const tab = (page: Page, name: RegExp) => page.getByRole("tablist", { name: "Chat status" }).getByRole("tab", { name });
const count = async (page: Page, name: RegExp) =>
  Number((await tab(page, name).locator("span.tabular-nums").innerText()).trim());

test("chat moves through new → replied → reply needed → completed, and in and out of spam", async ({ page }) => {
  await page.goto("/chats");
  const who = `E2E ${Date.now()}`;
  const phone = `+1555${String(Date.now()).slice(-7)}`;
  const t = await api(page, "POST", "/chats/threads",
    { channel: "sms", contact_phone: phone, contact_name: who, first_message: "Is my phone ready?" });
  expect(t.status).toBe("new");

  // New tab lists it, with a count.
  await page.reload();
  await expect(page.getByRole("button", { name: new RegExp(who) })).toBeVisible();   // list + counts loaded
  const newBefore = await count(page, /^New/);
  expect(newBefore).toBeGreaterThan(0);
  await tab(page, /^New/).click();
  await page.getByRole("button", { name: new RegExp(who) }).click();

  // Reply from the conversation → Replied.
  await page.getByPlaceholder("Message…").fill("Yes, come by any time.");
  await page.getByRole("button", { name: /^Send/ }).click();
  await expect(page.getByText("Yes, come by any time.")).toBeVisible();
  await expect.poll(() => count(page, /^New/)).toBe(newBefore - 1);
  await tab(page, /^Replied/).click();
  await page.getByRole("button", { name: new RegExp(who) }).click();
  await expect(page.getByRole("combobox", { name: "Chat status" })).toHaveValue("replied");

  // The customer writes back → Reply needed.
  await api(page, "POST", `/chats/threads/${t.id}/inbound`, { body: "Great, see you at 5." });
  await tab(page, /^Reply needed/).click();
  await page.getByRole("button", { name: new RegExp(who) }).click();
  await expect(page.getByRole("combobox", { name: "Chat status" })).toHaveValue("reply_needed");

  // Complete it by hand: it leaves the Reply needed tab.
  await page.getByRole("combobox", { name: "Chat status" }).selectOption("completed");
  await expect(page.getByText("Marked as completed")).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(who) })).toHaveCount(0);
  await tab(page, /^Completed/).click();
  await page.getByRole("button", { name: new RegExp(who) }).click();

  // Spam, then restore.
  const spamBefore = await count(page, /^Spam/);
  await page.getByRole("button", { name: "Mark as spam" }).click();
  await expect(page.getByText("Moved to spam")).toBeVisible();
  await expect.poll(() => count(page, /^Spam/)).toBe(spamBefore + 1);
  await tab(page, /^Spam/).click();
  await page.getByRole("button", { name: new RegExp(who) }).click();
  await page.getByRole("button", { name: /Not spam/ }).click();
  await expect(page.getByText("Moved back to the inbox")).toBeVisible();
  await expect.poll(() => count(page, /^Spam/)).toBe(spamBefore);
});

test("status tabs fit a phone screen without sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/chats");
  await expect(tab(page, /^All/)).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
