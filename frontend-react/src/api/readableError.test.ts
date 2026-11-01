import { describe, expect, it } from "vitest";
import { ApiError, readableError } from "./client";

describe("readableError", () => {
  it("shows the server's sentence, not the JSON", () => {
    expect(readableError('{"detail":"Nothing to order"}')).toBe("Nothing to order");
    expect(readableError('{"detail":[{"msg":"Field required"},{"msg":"Too long"}]}')).toBe("Field required; Too long");
    expect(readableError("Bad gateway")).toBe("Bad gateway");
    // F6: field problems name the field, and the same problem twice shows once
    expect(readableError(JSON.stringify({ detail: [
      { loc: ["body", "lines", 0, "line_type"], msg: "Field required" },
      { loc: ["body", "lines", 1, "line_type"], msg: "Field required" },
      { loc: ["body", "client_id"], msg: "Field required" },
      { loc: ["body", "client_id"], msg: "Field required" },
      { loc: ["body", "email"], msg: "Value error, Not a valid email" },
    ] }))).toBe("Line 1: line type is required; Line 2: line type is required; Client is required; Email: not a valid email");
    const e = new ApiError(400, '{"detail":"Nope"}');
    expect(e.message).toBe("Nope");
    expect(e.body).toBe('{"detail":"Nope"}');
  });
});
