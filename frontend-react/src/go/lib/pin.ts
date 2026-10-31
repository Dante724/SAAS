// Payment PIN calls. The PIN is sent to the server to check and never kept on the phone.
import { api, ApiError } from "../../api/client";

/** Same rules as the server (routers/auth.pin_problem), so mistakes show before sending. */
export function pinProblem(pin: string): string | null {
  if (!/^\d{4,6}$/.test(pin)) return "Use 4 to 6 digits";
  if (new Set(pin).size === 1) return "Pick a PIN that isn't one digit repeated";
  const steps = new Set([...pin].slice(1).map((c, i) => Number(c) - Number(pin[i])));
  if (steps.size === 1 && (steps.has(1) || steps.has(-1))) return "Pick a PIN that isn't a run like 1234";
  return null;
}

/** Null when the PIN is right, else the sentence to show. */
export async function verifyPin(pin: string): Promise<string | null> {
  if (!navigator.onLine) return "You're offline. Connect to check your PIN.";
  try {
    await api.post("/auth/pin/verify", { pin });
    return null;
  } catch (e) {
    const err = e as ApiError;
    if (err?.status === 429) {
      // Five wrong PINs signs the person out on the server; the next call sends them to sign in.
      api.get("/auth/me").catch(() => { /* silent: only nudges the client to notice the server sign-out */ });
      return err.message;
    }
    return err?.status === 403 ? "Wrong PIN. Try again." : err?.message || "Couldn't check your PIN";
  }
}

export async function setPin(pin: string, password: string): Promise<string | null> {
  const why = pinProblem(pin);
  if (why) return why;
  if (!navigator.onLine) return "You're offline. Connect to set your PIN.";
  try {
    await api.post("/auth/pin", { pin, password });
    return null;
  } catch (e) {
    return (e as ApiError)?.message || "Couldn't save your PIN";
  }
}
