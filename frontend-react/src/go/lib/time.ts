// Event times (started_at, status changes) are UTC instants; scheduled/due times are shop-local
// wall-clock. One parser for the whole app lives in lib/serverTime.
export { serverDate as utc } from "../../lib/serverTime";

export function hm(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`;
}
