import { useState, type FormEvent } from "react";
import { Loader2, Search } from "lucide-react";
import Button from "../components/Button";
import PublicLayout, { Field, PublicTitle, inputCls, publicCard } from "./PublicLayout";

// #6 — Re-findability. A customer who lost their tracking link can look their
// repair up with the order number + phone on file. Reuses the existing rate-limited
// POST /api/public/lookup, then hands off to /track/{token}.

export default function TrackLookup() {
  const [num, setNum] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!num.trim() || !phone.trim()) { setErr("Enter your order number and phone"); return; }
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/public/lookup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order_number: num.trim(), phone: phone.trim() }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "No matching order");
      window.location.assign(j.track_path || ("/track/" + j.token));
    } catch (e: any) { setErr(e.message || "No matching order — check the number and phone"); setBusy(false); }
  }

  return (
    <PublicLayout title="Track your repair">
      <PublicTitle title="Find my repair" sub="Enter the order number and the phone number on file." />
      <form onSubmit={submit} className={`${publicCard} mt-6 space-y-4 p-5 sm:p-6`} noValidate>
        <Field id="tl-number" label="Order number">
          <input id="tl-number" value={num} onChange={(e) => setNum(e.target.value)} placeholder="WO-00010" autoFocus className={inputCls} />
        </Field>
        <Field id="tl-phone" label="Phone number">
          <input id="tl-phone" value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" autoComplete="tel" placeholder="Your phone" className={inputCls} />
        </Field>
        {err && <p role="alert" className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700">{err}</p>}
        <Button type="submit" variant="primary" size="lg" busy={busy} className="w-full"
          icon={busy ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}>
          {busy ? "Searching…" : "Find my repair"}
        </Button>
      </form>
    </PublicLayout>
  );
}
