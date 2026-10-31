// Shared look for jobs: initials avatars, an icon per kind of item, and dates in plain words.
// Used by the Work orders list and the work order drawer so both read the same.
import { M } from "../../components/Shell";
import { hueText, tint } from "../../lib/tint";
import type { Order } from "./types";

// No red, amber or green: those mean status (overdue, waiting, done), and a customer must never look like one.
const AVATAR_HUES = ["#378ADD", "#7F77DD", "#185FA5", "#D4537E", "#0E7490", "#6D5BD0", "#475569", "#A855F7"];
export function Avatar({ name, size = 28 }: { name?: string | null; size?: number }) {
  const n = (name || "").trim();
  if (!n) return <span className="rounded-full bg-slate-100 grid place-items-center text-ink-tertiary flex-shrink-0" style={{ width: size, height: size }}><M n="person" c="text-[13px]" /></span>;
  const hue = AVATAR_HUES[[...n].reduce((a, ch) => a + ch.charCodeAt(0), 0) % AVATAR_HUES.length];
  const ini = n.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return <span className="rounded-full grid place-items-center font-semibold flex-shrink-0" style={{ width: size, height: size, background: tint(hue), color: hueText(hue), fontSize: size * 0.38 }}>{ini}</span>;
}

const DEVICE_ICON: [RegExp, string][] = [[/phone|iphone|galaxy|pixel|oneplus/i, "smartphone"], [/ipad|tab/i, "devices"], [/mac|laptop|book/i, "laptop"],
  [/watch/i, "devices"], [/boat|marine|engine|outboard|yacht/i, "sailing"], [/car|truck|vehicle|auto/i, "directions_car"], [/bike|cycle/i, "pedal_bike"]];
export const deviceIcon = (d: Order["device"]) => {
  const t = `${d?.asset_type || ""} ${d?.brand || ""} ${d?.model || ""}`;
  return DEVICE_ICON.find(([re]) => re.test(t))?.[1] || "devices";
};

// "Today 9:00", "Tomorrow", "Wed 16 Oct" — plain words for the date column.
export function friendlyDate(iso?: string | null) {
  if (!iso) return null;
  const d = new Date(iso); const t = new Date(); t.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((day.getTime() - t.getTime()) / 86400000);
  const time = d.getHours() || d.getMinutes() ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  const name = diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : diff === -1 ? "Yesterday" : d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  return `${name}${time && Math.abs(diff) <= 1 ? ` ${time}` : ""}`;
}
// Due date in words, red once it's past and the job is still open.
export function dueText(iso: string | null | undefined, open: boolean): [string, boolean] | null {
  if (!iso || !open) return null;
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const d = new Date(iso); const late = d.getTime() < Date.now();
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const days = Math.round((day.getTime() - t.getTime()) / 86400000);
  if (late) return [days >= 0 ? "Due earlier today" : `${-days} day${days === -1 ? "" : "s"} late`, true];
  return [days === 0 ? "Due today" : days === 1 ? "Due tomorrow" : `Due in ${days} days`, false];
}
