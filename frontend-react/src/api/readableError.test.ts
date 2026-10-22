import { describe, expect, it } from "vitest";
import { ApiError, readableError } from "./client";

describe("readableError", () => {
  it("shows the server's sentence, not the JSON", () => {
    expect(readableError('{"detail":"Nothing to order"}')).toBe("Nothing to order");
    expect(readableError('{"detail":[{"msg":"Field required"},{"msg":"Too long"}]}')).toBe("Field required; Too long");
    expect(readableError("Bad gateway")).toBe("Bad gateway");
    const e = new ApiError(400, '{"detail":"Nope"}');
    expect(e.message).toBe("Nope");
    expect(e.body).toBe('{"detail":"Nope"}');
  });
});
