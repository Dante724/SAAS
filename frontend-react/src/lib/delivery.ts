// What happened when we tried to send something to the customer (a message, a recommendation,
// an estimate, an invoice). The server always says plainly whether it really went out, so the
// screen never claims "sent" when email/text isn't set up or the customer has no contact.
export type DeliveryStatus = "sent" | "not_set_up" | "no_contact" | "failed";
export type Delivery = { status: DeliveryStatus; channel: "email" | "sms" | null; message: string };
export type Tone = "warn";
export type Flash = (m: string, tone?: Tone) => void;

/** The sentence to show for a send result. `fallback` covers an older server without `delivery`. */
export function deliveryText(d: Delivery | null | undefined, fallback: string): string {
  if (d && typeof d.message === "string" && d.message.trim()) return d.message;
  if (d && d.status && d.status !== "sent") return "Saved, but not sent to the customer.";
  return fallback;
}

/** Warning tone whenever the customer did not actually get it. */
export function deliveryTone(d: Delivery | null | undefined): Tone | undefined {
  return d && d.status && d.status !== "sent" ? "warn" : undefined;
}

/** Show a send result through a flash/toast callback. */
export function flashDelivery(flash: Flash, d: Delivery | null | undefined, fallback: string) {
  flash(deliveryText(d, fallback), deliveryTone(d));
}
