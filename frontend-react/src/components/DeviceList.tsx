import { useCallback, useEffect, useState } from "react";
import { Laptop, Smartphone } from "lucide-react";
import { api, readableError } from "../api/client";
import { confirmDialog } from "../lib/dialog";

// Signed-in devices, shared by your own Security panel and an admin's view of a teammate
// (Team → person → Sign-in). One row per sign-in on one device; signing a row out ends that
// device's session at once.

export type Device = {
  id: string; device: string; ip: string | null;
  signed_in_at: string | null; last_active_at: string | null; this_device: boolean;
};

const PHONE = /iPhone|iPad|Android|Tekly Go/;

export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const mins = Math.round((now - new Date(iso).getTime()) / 60000);
  if (mins < 2) return "Active now";
  if (mins < 60) return `Active ${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `Active ${hrs} h ago`;
  const days = Math.round(hrs / 24);
  return `Active ${days} day${days === 1 ? "" : "s"} ago`;
}

const day = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" }) : "";

export function DeviceRow({ d, action }: { d: Device; action?: React.ReactNode }) {
  const Icon = PHONE.test(d.device) ? Smartphone : Laptop;
  const meta = [d.ip, d.signed_in_at && `Signed in ${day(d.signed_in_at)}`, ago(d.last_active_at)].filter(Boolean).join(" · ");
  return (
    <div className="flex items-center gap-3 py-2.5">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-ink-secondary"><Icon size={16} /></span>
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-[13px] font-semibold text-ink-primary">
          <span className="truncate">{d.device}</span>
          {d.this_device && <span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-ink-secondary">This device</span>}
        </p>
        <p className="truncate text-[12px] text-ink-tertiary">{meta}</p>
      </div>
      {action}
    </div>
  );
}

export default function DeviceList({ base, person }: {
  /** "/auth/sessions" for your own; "/staff/employees/{id}/sessions" for a teammate. */
  base: string;
  /** Set for an admin looking at someone else: changes the wording and the bulk action. */
  person?: string;
}) {
  const [items, setItems] = useState<Device[] | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [hidden, setHidden] = useState(false);
  const load = useCallback(() => {
    setErr("");
    api.get<{ items: Device[] }>(base).then((r) => setItems(r.items || []))
      // Not yours to manage (e.g. the shop owner's account, seen by another admin): show nothing.
      .catch((e: any) => (e?.status === 403 ? setHidden(true) : setErr("Couldn't load devices.")));
  }, [base]);
  useEffect(() => { load(); }, [load]);

  async function signOut(d: Device) {
    setErr(""); setNote("");
    setItems((xs) => (xs || []).filter((x) => x.id !== d.id));
    try { await api.post(`${base}/${d.id}/sign-out`, {}); setNote(`Signed out of ${d.device}.`); }
    catch (e: any) { setErr(readableError(e?.body || e?.message || "") || "Couldn't sign that device out."); load(); }
  }

  const others = (items || []).filter((d) => !d.this_device);
  async function signOutAll() {
    const n = person ? (items || []).length : others.length;
    const ok = await confirmDialog(
      person ? `${person} will need to sign in again on every device.`
        : "You'll stay signed in here. Everywhere else, you'll need your password again.",
      { title: person ? `Sign ${person} out everywhere?` : "Sign out other devices?",
        confirmLabel: `Sign out ${n} device${n === 1 ? "" : "s"}`, danger: true });
    if (!ok) return;
    setErr(""); setNote("");
    try {
      await api.post(person ? `${base}/sign-out-all` : `${base}/sign-out-others`, {});
      setNote(person ? `${person} is signed out everywhere.` : "Signed out of your other devices.");
      load();
    } catch (e: any) { setErr(readableError(e?.body || e?.message || "") || "Couldn't sign those devices out."); }
  }

  if (hidden) return <p className="py-1 text-xs text-ink-tertiary">Only the shop owner can manage these devices.</p>;
  return (
    <div>
      {items === null && !err && (
        <div className="space-y-2 py-1" aria-label="Loading devices">
          {[0, 1].map((i) => <div key={i} className="sk h-10 rounded-lg" />)}
        </div>
      )}
      {err && (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {err} <button onClick={load} className="font-semibold underline">Retry</button>
        </p>
      )}
      {items && items.length === 0 && !err && (
        <p className="py-2 text-xs text-ink-tertiary">{person ? `${person} isn't signed in anywhere.` : "No devices are signed in."}</p>
      )}
      {items && items.length > 0 && (
        <div className="divide-y divide-slate-100">
          {items.map((d) => (
            <DeviceRow key={d.id} d={d} action={!d.this_device && (
              <button onClick={() => signOut(d)} className="shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-ink-secondary hover:bg-slate-100 hover:text-ink-primary">
                Sign out</button>)} />
          ))}
        </div>
      )}
      {items && items.length === 1 && items[0].this_device && !person && (
        <p className="pt-1 text-xs text-ink-tertiary">Only this device is signed in.</p>
      )}
      {note && <p className="pt-2 text-xs text-ink-secondary" role="status">{note}</p>}
      {items && ((person && items.length > 0) || (!person && others.length >= 1)) && (
        <button onClick={signOutAll} className="mt-2 text-xs font-semibold text-rose-600 hover:underline">
          {person ? "Sign out everywhere" : "Sign out all other devices"}</button>
      )}
    </div>
  );
}
