// Vitest: which fetched document types may be opened in a same-origin tab (blob URL).
import { describe, it, expect } from "vitest";
import { isInlineSafeType } from "./client";

describe("isInlineSafeType", () => {
  it.each(["application/pdf", "image/png", "image/jpeg", "image/webp", "video/mp4", "audio/mpeg",
           "text/plain; charset=utf-8"])("opens %s", (t) => expect(isInlineSafeType(t)).toBe(true));

  it.each(["text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", "application/xml",
           "application/javascript", "application/json", "text/csv", "application/octet-stream", "",
           "text/html; charset=utf-8", "image/svg+xml;foo", "application/pdfx"])(
    "downloads %s instead", (t) => expect(isInlineSafeType(t)).toBe(false));
});
