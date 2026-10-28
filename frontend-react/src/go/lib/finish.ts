// Finishing a job from the phone (B11). The close is checked before any money moves, and
// "Finish & take" pays and closes in one server call (/orders/{id}/finish): if the job can't
// close, no money is taken; if the payment fails, the job stays open.

export type FinishCheck = { ok: true } | { ok: false; code: string; title: string; message: string };
type Post = <T>(path: string, body: unknown) => Promise<T>;

/** Would finishing into `status` go through? `paying`: the payment is taken with the close. */
export async function checkFinish(post: Post, orderId: string, status: string, statusName: string, paying: boolean): Promise<FinishCheck> {
  const r = await post<{ ok: boolean; code?: string; message?: string }>(
    `/orders/${encodeURIComponent(orderId)}/status/check${paying ? "?assume_paid=true" : ""}`, { status });
  if (r.ok) return { ok: true };
  const code = r.code || "blocked";
  return {
    ok: false, code,
    title: code === "unpaid" ? "Still to pay" : `Can't move to ${statusName} yet`,
    message: r.message || "Something on the job needs finishing first.",
  };
}

export type FinishPayment = { method: "cash" | "cashless" | "card"; account_id: string; amount: number };

/** Take the payment and finish the job in one step. Retries with the same key never pay twice. */
export function finishWithPayment(postOnce: <T>(p: string, b: unknown, key: string) => Promise<T>, orderId: string,
  status: string, payment: FinishPayment, key: string) {
  return postOnce<{ ok: boolean; status: string; payment: { amount: number } | null }>(
    `/orders/${encodeURIComponent(orderId)}/finish`, { status, payment }, key);
}
