import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

// Team E user flows, end to end against a running app (see e2e/README.md):
//  1. A technician opens My earnings from the account menu and sees only their own pay:
//     their job and line items, never a colleague's, and the shop's payroll stays closed to them.
//  2. A checklist set to "attach automatically when the order enters In repair" lands on the job
//     once when it reaches that status, however often it goes back there.
// The signed-in E2E user (E2E_EMAIL) must be the shop owner. Every name carries a run stamp, so
// the specs can run again against the same shop.

const STAMP = `${Date.now().toString(36)}`;
const PASSWORD = `pw-e2e-${STAMP}-Xy9`;

async function ownerApi(page: Page): Promise<{ api: APIRequestContext; H: Record<string, string> }> {
  await page.goto("/");
  const token = await page.evaluate(() => localStorage.getItem("ros_tok") || localStorage.getItem("ros_token") || localStorage.getItem("token") || "");
  return { api: page.request, H: token ? { Authorization: `Bearer ${token}` } : {} };
}

async function ok<T = any>(res: Promise<import("@playwright/test").APIResponse>): Promise<T> {
  const r = await res;
  expect(r.ok(), `${r.url()} → ${r.status()} ${await r.text()}`).toBeTruthy();
  return r.json();
}

test.describe("My earnings", () => {
  test("a technician sees only their own earnings", async ({ page, browser }) => {
    const { api, H } = await ownerApi(page);
    const loc = (await ok(api.get("/api/staff/locations", { headers: H }))).items[0].id;
    const role = await ok(api.post("/api/settings/rbac/roles", { headers: H, data: { name: `E2E tech ${STAMP}`, template: "Specialist" } }));
    const people: Record<"a" | "b", { id: string; email: string; order: string }> = {} as any;
    for (const who of ["a", "b"] as const) {
      const email = `e2e-${who}-${STAMP}@example.com`;
      const emp = await ok(api.post("/api/staff/employees", { headers: H, data: { name: `E2E Tech ${who.toUpperCase()} ${STAMP}`, email, password: PASSWORD, role_id: role.id } }));
      await ok(api.post("/api/payroll/rules", { headers: H, data: { name: `E2E labour ${who} ${STAMP}`, event: "labor_performed", calc_type: "percent", amount: 10, employee_id: emp.id } }));
      const order = await ok(api.post("/api/orders", { headers: H, data: { location_id: loc, complaint: "E2E", assigned_to_id: emp.id } }));
      await ok(api.post(`/api/orders/${order.id}/lines`, { headers: H, data: { line_type: "labour", kind: "custom", description: `E2E repair ${who} ${STAMP}`, qty: 1, unit_price: who === "a" ? 120 : 900, technician_id: emp.id } }));
      await ok(api.patch(`/api/orders/${order.id}/status`, { headers: H, data: { status: "done" } }));
      people[who] = { id: emp.id, email, order: order.order_number };
    }

    // Sign in as technician A in a fresh browser.
    const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const tech = await ctx.newPage();
    await tech.goto("/login");
    await tech.getByLabel(/email/i).fill(people.a.email);
    await tech.getByRole("button", { name: /continue/i }).click();
    await tech.getByLabel(/^password/i).fill(PASSWORD);
    await tech.getByRole("button", { name: /sign in|log in/i }).click();
    await expect(tech).not.toHaveURL(/\/login/);

    await tech.getByRole("button", { name: "Account menu" }).click();
    await tech.getByRole("link", { name: "My earnings" }).click();
    await expect(tech).toHaveURL(/\/my-earnings$/);
    await expect(tech.getByTestId("earnings-total")).toBeVisible();

    await tech.getByRole("button", { name: new RegExp(`E2E labour a ${STAMP}`) }).click();
    await expect(tech.getByRole("link", { name: people.a.order })).toBeVisible();
    await tech.getByRole("button", { name: "Show line items" }).first().click();
    await expect(tech.getByText(`E2E repair a ${STAMP}`)).toBeVisible();
    // Nothing of technician B's.
    await expect(tech.getByText(`E2E labour b ${STAMP}`)).toHaveCount(0);
    await expect(tech.getByText(people.b.order, { exact: true })).toHaveCount(0);

    // The API agrees: /payroll/me is theirs, the shop's payroll is closed to them.
    const token = await tech.evaluate(() => localStorage.getItem("ros_tok") || localStorage.getItem("ros_token") || "");
    const T = { Authorization: `Bearer ${token}` };
    const me = await ok(tech.request.get("/api/payroll/me", { headers: T }));
    expect(me.employee_id).toBe(people.a.id);
    expect(me.commissions.map((g: any) => g.rule_name)).not.toContain(`E2E labour b ${STAMP}`);
    expect((await tech.request.get(`/api/payroll/calculation/detail?employee_id=${people.b.id}&month=${new Date().toISOString().slice(0, 7)}`, { headers: T })).status()).toBe(403);
    await ctx.close();
  });
});

test.describe("Checklist auto-attach", () => {
  test("a template set to In repair attaches once when the job gets there", async ({ page }) => {
    const name = `E2E repair QC ${STAMP}`;
    await page.goto("/settings");
    await page.getByRole("button", { name: "Checklists" }).first().click();
    await page.getByRole("button", { name: "New checklist" }).click();
    const editor = page.getByRole("dialog", { name: "New checklist" });
    await editor.getByLabel("Checklist name").fill(name);
    await editor.getByLabel("Check name").first().fill("Device powers on");
    await editor.getByRole("group", { name: "Work orders statuses" }).getByRole("button", { name: "In repair" }).click();
    await expect(editor.getByText(/Added to the job once/)).toBeVisible();
    await editor.getByRole("button", { name: "Create checklist" }).click();
    await expect(editor).toBeHidden();

    const { api, H } = await ownerApi(page);
    const tpl = (await ok(api.get("/api/checklist-templates", { headers: H }))).items.find((t: any) => t.name === name);
    expect(tpl.auto_status_keys).toEqual([{ record_type: "work_order", key: "in_repair" }]);

    const loc = (await ok(api.get("/api/staff/locations", { headers: H }))).items[0].id;
    const order = await ok(api.post("/api/orders", { headers: H, data: { location_id: loc, complaint: "E2E auto-attach" } }));
    for (const status of ["in_repair", "diagnosed", "in_repair"]) {
      await ok(api.patch(`/api/orders/${order.id}/status`, { headers: H, data: { status } }));
    }
    const cls = (await ok(api.get(`/api/orders/${order.id}/checklists`, { headers: H }))).items;
    expect(cls.filter((c: any) => c.title === name)).toHaveLength(1);

    // And the job shows it.
    await page.goto(`/orders?open=${order.id}`);
    await page.getByRole("button", { name: /^Checklists/ }).first().click();
    await expect(page.getByText(`${name} (0 of 1)`)).toBeVisible();   // the checklist card, not the template picker

    // Clean up the template so later runs start from the same shop state.
    await ok(api.delete(`/api/checklist-templates/${tpl.id}`, { headers: H }));
  });
});
