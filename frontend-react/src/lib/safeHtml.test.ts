// Vitest: template/preview HTML is stripped of anything that could run script in this origin.
import { describe, it, expect } from "vitest";
import { escapeHtml, sanitizeHtml } from "./safeHtml";

describe("sanitizeHtml", () => {
  it.each([
    ['<img src=x onerror="alert(1)">', "onerror"],
    ["<script>alert(1)</script><p>ok</p>", "<script"],
    ['<a href="javascript:alert(1)">x</a>', "javascript:"],
    ['<a href=" jav&#x09;ascript:alert(1)">x</a>', "ascript:"],
    ['<iframe src="https://evil"></iframe>', "<iframe"],
    ['<svg><foreignObject><img src=x onerror=alert(1)></foreignObject></svg>', "onerror"],
    ['<svg><a><animate attributeName="href" values="javascript:alert(1)"/></a></svg>', "animate"],
    ['<div onmouseover="x()">hi</div>', "onmouseover"],
    ['<object data="data:text/html,<script>alert(1)</script>"></object>', "<object"],
    ['<img src="data:text/html;base64,PHNjcmlwdD4=">', "data:text/html"],
  ])("removes %s", (input, banned) => {
    expect(sanitizeHtml(input).toLowerCase()).not.toContain(banned.toLowerCase());
  });

  it("keeps ordinary document markup, styles, tokens and inline image logos", () => {
    const html = '<table style="width:100%"><tr><td class="x">{Client Name}</td></tr></table>' +
                 '<img src="data:image/png;base64,iVBORw0KGgo=" alt="logo"><b>Total</b>';
    const out = sanitizeHtml(html);
    expect(out).toContain("{Client Name}");
    expect(out).toContain('style="width:100%"');
    expect(out).toContain('src="data:image/png;base64,iVBORw0KGgo="');
    expect(out).toContain("<b>Total</b>");
  });
});

describe("escapeHtml", () => {
  it("escapes markup and quotes", () =>
    expect(escapeHtml(`<img src="x" onerror='y'>&`)).toBe("&lt;img src=&quot;x&quot; onerror=&#39;y&#39;&gt;&amp;"));
  it("handles null/undefined", () => expect(escapeHtml(undefined)).toBe(""));
});

describe("safeHttpsUrl", () => {
  it("keeps https links and drops everything else", async () => {
    const { safeHttpsUrl } = await import("./safeHtml");
    expect(safeHttpsUrl("https://rec.example.com/a.mp3")).toBe("https://rec.example.com/a.mp3");
    expect(safeHttpsUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpsUrl(" JavaScript:alert(1)")).toBeNull();
    expect(safeHttpsUrl("data:text/html,<script>1</script>")).toBeNull();
    expect(safeHttpsUrl("http://rec.example.com/a.mp3")).toBeNull();
    expect(safeHttpsUrl(null)).toBeNull();
  });
});
