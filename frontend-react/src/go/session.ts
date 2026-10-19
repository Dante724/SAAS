// Sign-in / sign-out for Tekly Go. The session is the same bearer token the web app uses
// (localStorage "ros_tok"); signing in happens on the shared /login page, which sends people back here.
import { goQueue } from "./lib/offlineQueue";
import { disablePush } from "./lib/push";

export const LOGIN_URL = "/login?next=" + encodeURIComponent("/go/today");

export function hasToken(): boolean {
  try { return !!(localStorage.getItem("ros_tok") || localStorage.getItem("ros_token") || localStorage.getItem("token")); }
  catch { return false; }
}

export async function signOut() {
  const q = goQueue();
  const { pending } = await q.status();
  if (pending && !window.confirm(`${pending} change${pending > 1 ? "s haven't" : " hasn't"} been sent yet. Signing out deletes ${pending > 1 ? "them" : "it"}. Sign out anyway?`)) return;
  await q.clear().catch(() => {});
  await disablePush().catch(() => {});   // before the token goes: unsubscribing needs it
  try { await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${localStorage.getItem("ros_tok") || ""}` } }); } catch { /* best effort */ }
  try { localStorage.removeItem("ros_tok"); } catch { /* ignore */ }
  navigator.serviceWorker?.controller?.postMessage("CLEAR_DATA");
  window.location.replace(LOGIN_URL);
}
