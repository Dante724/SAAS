import { describe, it, expect } from "vitest";
import { sanitizeHtml } from "../../../lib/safeHtml";
import { SEEDED_TEMPLATES } from "./seededTemplates.fixture";
import {
  canonical, chipLabel, chipsToTokens, cleanTemplate, placeholders, sameTemplate, scopeCss, splitLeadingStyles, staticReasons,
  textBetweenRows, tokensToChips,
} from "./templateHtml";

const chip = (t: string, block: boolean) => `<${block ? "div" : "span"} data-tok="${t.replace(/"/g, "&quot;")}"></${block ? "div" : "span"}>`;

describe("placeholders", () => {
  it("finds variables, conditions and component blocks in order", () => {
    const html = "<p>{Client Name}</p>{#if Paid}<p>PAID {Order Total}</p>{#else}<p>due</p>{/if}{{component: Footer}}";
    expect(placeholders(html)).toEqual(["{Client Name}", "{#if Paid}", "{Order Total}", "{#else}", "{/if}", "{{component: Footer}}"]);
  });
  it("labels chips in plain words", () => {
    expect(chipLabel("{Client Name}")).toBe("Client Name");
    expect(chipLabel("{#if Paid}")).toBe("if Paid");
    expect(chipLabel("{/unless}")).toBe("end unless");
    expect(chipLabel("{{component: Footer}}")).toBe("component: Footer");
  });
});

describe("chips round-trip", () => {
  // (The sales receipt is the exception the views grey out: see below.)
  it.each(Object.entries(SEEDED_TEMPLATES).filter(([n]) => n !== "Sales receipt"))("%s keeps every placeholder in place", (_name, html) => {
    const { styles, body } = splitLeadingStyles(html);
    const back = styles + chipsToTokens(tokensToChips(body, chip));
    expect(sameTemplate(back, html)).toBe(true);
    expect(placeholders(back)).toEqual(placeholders(html));
  });
  it("never turns placeholders inside attributes into chips", () => {
    const out = tokensToChips('<img alt="{Company Name}" src="logo.png"><p>{Company Name}</p>', chip);
    expect(out).toContain('alt="{Company Name}"');
    expect(out).toContain('<span data-tok="{Company Name}">');
  });
  it("turns a placeholder standing between blocks into one block chip with its exact text", () => {
    const out = tokensToChips("<p>a</p>\n{#if Paid}\n<p>b</p>{/if}", chip);
    expect(out).toContain('<div data-tok="{#if Paid}">');
    expect(out).toContain('<div data-tok="{/if}">');
  });
});

describe("canonical", () => {
  it("ignores attribute order, style spacing and b/strong", () => {
    expect(sameTemplate('<p style="color: red;margin:0" class="a b">x <b>y</b></p>', '<p class="b a" style="margin: 0px; color:red">x <strong>y</strong></p>')).toBe(true);
  });
  it("notices a dropped attribute, tag or character", () => {
    expect(sameTemplate('<table><tbody><tr><td style="width:50%">a</td></tr></tbody></table>', "<table><tbody><tr><td>a</td></tr></tbody></table>")).toBe(false);
    expect(sameTemplate("<div>{Client Name}</div>", "<div><p>{Client Name}</p></div>")).toBe(false);
    expect(sameTemplate("<p>{Client Name}</p>", "<p>{Client name}</p>")).toBe(false);
  });
  it("keeps style blocks", () => {
    expect(canonical("<style>.a{color:red}</style><p>x</p>")).toContain("<style>.a{color:red}</style>");
  });
});

describe("constructs a visual view can't hold", () => {
  it("spots a condition between table rows (the seeded sales receipt)", () => {
    expect(textBetweenRows(SEEDED_TEMPLATES["Sales receipt"])).toContain("{#if Total Discount}");
    const r = staticReasons(SEEDED_TEMPLATES["Sales receipt"]);
    expect(r.design).toMatch(/\{#if Total Discount\} between table rows/);
    expect(r.text).toMatch(/between table rows/);
    expect(r.html).toBeNull();
  });
  it("leaves conditions inside a cell or a paragraph alone", () => {
    expect(textBetweenRows("<table><tbody><tr><td>{#if Paid}PAID{/if}</td></tr></tbody></table>")).toBeNull();
    expect(staticReasons("<p>{#if Paid}PAID{/if}</p>").design).toBeNull();
  });
  it("spots a style block in the middle of the page", () => {
    expect(staticReasons("<p>a</p><style>p{color:red}</style>").design).toMatch(/style block/);
    expect(staticReasons("<style>p{color:red}</style><p>a</p>").design).toBeNull();
  });
});

describe("cleanTemplate", () => {
  it.each(Object.entries(SEEDED_TEMPLATES))("saves the untouched %s byte for byte", (_name, html) => {
    expect(cleanTemplate(html, sanitizeHtml)).toBe(html);
  });
  it("still strips script and event handlers", () => {
    const out = cleanTemplate('<p onclick="steal()">{Client Name}</p><script>alert(1)</script>', sanitizeHtml);
    expect(out).not.toMatch(/onclick|<script/);
    expect(out).toContain("{Client Name}");
  });
  it("keeps conditions between table rows in place while cleaning", () => {
    const html = '<table><tbody>{#if Tax Value}<tr><td onclick="x()">Tax</td></tr>{/if}</tbody></table>';
    expect(cleanTemplate(html, sanitizeHtml)).toBe("<table><tbody>{#if Tax Value}<tr><td>Tax</td></tr>{/if}</tbody></table>");
  });
});

describe("scopeCss", () => {
  it("limits the template's CSS to the sheet and drops @-rules", () => {
    const out = scopeCss("body{margin:2em} tr, td {height:2em} @page{size:A4}", ".sheet");
    expect(out).toContain(".sheet{margin:2em}");
    expect(out).toContain(".sheet tr, .sheet td{height:2em}");
    expect(out).not.toContain("@page");
  });
});
