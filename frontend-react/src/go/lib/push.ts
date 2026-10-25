// Push notifications for Tekly Go, on the /go/ service worker (same server API as the /tech app:
// /api/push/vapid-public-key, /subscribe, /unsubscribe). iPhone needs Tekly Go added to the Home
// Screen (iOS 16.4+) before the browser offers push at all.
import { api } from "../../api/client";

export type PushState = "unsupported" | "not-configured" | "off" | "on" | "denied";

/** Where a notification's link opens. Server links still use the /tech app's form. Pure and tested;
 *  public/go/sw.js keeps a copy (a service worker can't import this module). */
export function goPath(link?: string | null): string {
  const p = (link || "").replace(/^https?:\/\/[^/]+/, "");
  const order = p.match(/^\/tech\/?#\/order\/([^/?#]+)/) || p.match(/^\/orders\?(?:.*&)?open=([^&#]+)/);
  if (order) return `/go/job/${order[1]}`;
  if (/^\/(?:inquiries|leads)\/?(?:[?#]|$)/.test(p)) {
    const lead = p.match(/[?&]lead=([^&#]+)/);
    return lead ? `/go/inquiries?lead=${lead[1]}` : "/go/inquiries";
  }
  if (!p || /^\/tech\/?(#.*)?$/.test(p)) return "/go/today";
  return p.startsWith("/") ? p : "/go/today";
}

function b64ToBytes(b64: string): Uint8Array {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

const supported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

async function goRegistration(): Promise<ServiceWorkerRegistration | undefined> {
  return navigator.serviceWorker.getRegistration("/go/");
}

export async function pushState(): Promise<PushState> {
  if (!supported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  const reg = await goRegistration();
  const sub = await reg?.pushManager.getSubscription();
  return sub ? "on" : "off";
}

export async function enablePush(): Promise<PushState> {
  if (!supported()) return "unsupported";
  const key = await api.get<{ enabled: boolean; public_key: string }>("/push/vapid-public-key");
  if (!key.enabled || !key.public_key) return "not-configured";
  const perm = await Notification.requestPermission();   // must run from the tap (iOS)
  if (perm !== "granted") return perm === "denied" ? "denied" : "off";
  const reg = (await goRegistration()) ?? (await navigator.serviceWorker.register("/go/sw.js", { scope: "/go/" }));
  await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription())
    ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key.public_key) as BufferSource }));
  const j = sub.toJSON();
  await api.post("/push/subscribe", { endpoint: j.endpoint, keys: j.keys, user_agent: navigator.userAgent });
  return "on";
}

/** Stop this phone getting notifications (also run on sign-out, so the next person doesn't get yours). */
export async function disablePush(): Promise<PushState> {
  if (!supported()) return "unsupported";
  const sub = await (await goRegistration())?.pushManager.getSubscription();
  if (sub) {
    await api.post("/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe().catch(() => {});
  }
  return "off";
}
