import { describe, it, expect, vi } from "vitest";
import { deliveryText, deliveryTone, flashDelivery } from "./delivery";

describe("delivery", () => {
  it("uses the server's sentence and warns when it wasn't sent", () => {
    const d = { status: "no_contact" as const, channel: null, message: "Saved on the job, but not sent: the customer has no phone number or email." };
    expect(deliveryText(d, "Sent")).toBe(d.message);
    expect(deliveryTone(d)).toBe("warn");
    const f = vi.fn(); flashDelivery(f, d, "Sent"); expect(f).toHaveBeenCalledWith(d.message, "warn");
  });
  it("never falls back to a success sentence for a non-sent result without a message", () => {
    expect(deliveryText({ status: "failed", channel: "email", message: "" }, "Sent to the customer")).not.toMatch(/^Sent/);
  });
  it("uses the fallback only when the server sent no delivery", () => {
    expect(deliveryText(undefined, "Shown on the tracking page")).toBe("Shown on the tracking page");
    expect(deliveryTone({ status: "sent", channel: "sms", message: "Sent by text to the customer." })).toBeUndefined();
  });
});
