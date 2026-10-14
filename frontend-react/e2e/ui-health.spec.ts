import { test, expect, type Page } from "@playwright/test";

// App-wide UI health: for each main screen we (1) take a screenshot so `--update-snapshots`
// gives a visual-regression baseline that catches misaligned boxes / spacing drift, and
// (2) assert no VISIBLE label renders a raw snake_case key (e.g. "waiting_parts" instead of
// "Waiting parts"). The two things unit tests can't see: appearance, and rendered text.

const PAGES: [name: string, path: string][] = [
  ["home", "/"],
  ["work-orders", "/orders"],
  ["appointments", "/appointments"],
  ["inventory", "/inventory"],
  ["invoices", "/invoices"],
  ["clients", "/clients"],
  ["reports", "/reports"],
  ["timesheets", "/timesheets"],
  ["settings", "/settings"],
];

// Elements whose text is a human label (NOT the intentional mono key badges). We scan these
// for underscores. The status-key badge in settings is `font-mono` + muted, so excluding
// mono text keeps the check focused on real labels.
async function visibleLabelUnderscores(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = [];
    // Skip (a) mono key badges — the intentional status-key slugs — and (b) Material Symbols
    // icon spans, whose textContent is the ligature name (e.g. "calendar_month") but renders
    // as a glyph. Both legitimately contain underscores and are NOT labels.
    const skip = (el: Element) => {
      const cs = getComputedStyle(el as HTMLElement);
      const ff = cs.fontFamily.toLowerCase();
      return ff.includes("mono") || ff.includes("material")
        || el.className.toString().toLowerCase().includes("material-symbols");
    };
    const sel = "h1,h2,h3,h4,button,a,th,td,label,[role='button'],.pill,.badge,span,p,li,div";
    document.querySelectorAll(sel).forEach((el) => {
      if (el.children.length > 0) return;          // leaf text only
      if ((el as HTMLElement).offsetParent === null) return;  // visible only
      if (skip(el)) return;
      const t = (el.textContent || "").trim();
      if (t.length < 40 && /^[A-Za-z]*[a-z]{2,}_[a-z]{2,}/.test(t) && !/\s{2,}/.test(t)) bad.push(t);
    });
    return Array.from(new Set(bad));
  });
}

for (const [name, path] of PAGES) {
  test(`${name}: visual baseline + no underscored labels`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    // 1) rendered-text check
    const leaks = await visibleLabelUnderscores(page);
    expect(leaks, `raw snake_case shown as a label on ${path}`).toEqual([]);
    // 2) visual baseline (first run with --update-snapshots creates it)
    await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true, animations: "disabled" });
  });
}
