import { describe, it, expect, vi } from "vitest";
import { checkFinish, finishWithPayment } from "./finish";

describe("checkFinish (B11: validate the close before taking money)", () => {
  it("asks the server with the payment counted as taken", async () => {
    const post = vi.fn(async () => ({ ok: true })) as any;
    expect(await checkFinish(post, "o 1", "done", "Done", true)).toEqual({ ok: true });
    expect(post).toHaveBeenCalledWith("/orders/o%201/status/check?assume_paid=true", { status: "done" });
  });

  it("explains a refusal in plain words", async () => {
    const post = vi.fn(async () => ({ ok: false, code: "blocked", message: "Cannot complete: 1 required checklist item(s) not done." })) as any;
    expect(await checkFinish(post, "o1", "done", "Done", true)).toEqual({
      ok: false, code: "blocked", title: "Can't move to Done yet", message: "Cannot complete: 1 required checklist item(s) not done.",
    });
  });

  it("names an unpaid balance when this person can't take the payment", async () => {
    const post = vi.fn(async () => ({ ok: false, code: "unpaid", message: "This job still has 178.00 to pay." })) as any;
    const r = await checkFinish(post, "o1", "done", "Done", false);
    expect(r).toMatchObject({ ok: false, code: "unpaid", title: "Still to pay" });
    expect(post).toHaveBeenCalledWith("/orders/o1/status/check", { status: "done" });
  });
});

describe("finishWithPayment", () => {
  it("pays and closes in one call, keyed so a retry can't pay twice", async () => {
    const postOnce = vi.fn(async () => ({ ok: true, status: "done", payment: { amount: 178 } })) as any;
    await finishWithPayment(postOnce, "o1", "done", { method: "cash", account_id: "a1", amount: 178 }, "k1");
    expect(postOnce).toHaveBeenCalledTimes(1);
    expect(postOnce).toHaveBeenCalledWith("/orders/o1/finish", { status: "done", payment: { method: "cash", account_id: "a1", amount: 178 } }, "k1");
  });
});
