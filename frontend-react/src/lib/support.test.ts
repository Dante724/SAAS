import { describe, expect, it } from "vitest";
import { supportContact } from "./support";

describe("supportContact", () => {
  it("defaults to mailing hello@tekly.app", () => {
    expect(supportContact(null)).toEqual({ email: "hello@tekly.app", href: "mailto:hello@tekly.app", external: false });
  });
  it("uses the configured email", () => {
    expect(supportContact({ support: { email: "help@shop.example", url: "" } }).href).toBe("mailto:help@shop.example");
  });
  it("prefers the configured help URL, but only http(s)", () => {
    expect(supportContact({ support: { email: "a@b.c", url: "https://help.example" } })).toMatchObject({ href: "https://help.example", external: true });
    expect(supportContact({ support: { email: "a@b.c", url: "javascript:alert(1)" } }).href).toBe("mailto:a@b.c");
  });
});
