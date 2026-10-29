// Deep links that open the phone's own SMS / WhatsApp app with the number and text filled in.
// Built on the phone only; numbers and text never go into a URL sent to our server (design §3.4).

export function isIOS(ua = typeof navigator !== "undefined" ? navigator.userAgent : ""): boolean {
  return /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && typeof document !== "undefined" && "ontouchend" in document);
}

/** iOS wants `sms:+44…&body=`, Android `sms:+44…?body=`. */
export function smsLink(number: string, body: string, ios = isIOS()): string {
  const n = (number || "").replace(/[^\d+]/g, "");
  return `sms:${n}${ios ? "&" : "?"}body=${encodeURIComponent(body)}`;
}

/** wa.me takes the international number without the "+". */
export function waLink(e164: string, body: string): string {
  return `https://wa.me/${(e164 || "").replace(/\D/g, "")}?text=${encodeURIComponent(body)}`;
}

/** How many SMS a text costs: 160 characters for one, 153 each when split (GSM-7). Emoji or
 *  other non-GSM characters switch to 70 / 67. */
export function smsSegments(text: string): number {
  if (!text) return 0;
  const gsm = /^[\n\r\x20-\x7E£¥èéùìòÇØøÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ¡ÄÖÑÜ§¿äöñüà€]*$/.test(text);
  const [one, many] = gsm ? [160, 153] : [70, 67];
  return text.length <= one ? 1 : Math.ceil(text.length / many);
}
