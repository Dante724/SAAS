import { useEffect, useState, type FormEvent } from "react";
import { Loader2, Search } from "lucide-react";

// #6 — Re-findability. A customer who lost their tracking link can look their
// repair up with the order number + phone on file. Reuses the existing rate-limited
// POST /api/public/lookup, then hands off to /track/{token}.

const GRAD = "linear-gradient(135deg,#2c72d9,#6848be)";

export default function TrackLookup() {
  const [num, setNum] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const set = () => document.documentElement.classList.toggle("dark", mq.matches);
      set(); mq.addEventListener("change", set);
    } catch { /* ignore */ }
  }, []);

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
    <div className="min-h-screen bg-surface font-sans text-ink-primary">
      <div className="mx-auto grid min-h-screen max-w-md place-items-center px-5">
        <div className="w-full">
          <div className="mb-6 flex items-center gap-3">
            <div className="grid h-12 w-12 place-items-center rounded-2xl text-lg font-bold text-white" style={{ background: GRAD }}>R</div>
            <div>
              <div className="text-xl font-bold tracking-[-.3px]">Track your repair</div>
              <div className="text-xs font-semibold text-ink-tertiary">Look it up with your details</div>
            </div>
          </div>
          <form onSubmit={submit} className="rounded-2xl border border-slate-200 bg-white p-6 sm:p-7">
            <h1 className="text-lg font-bold text-ink-primary">Find my repair</h1>
            <p className="mt-1 text-[13px] text-ink-tertiary">Enter the order number and the phone number on file.</p>

            <label className="mt-5 block text-[13px] font-bold text-ink-secondary">Order number</label>
            <input value={num} onChange={(e) => setNum(e.target.value)} placeholder="WO-00010" autoFocus
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary" />

            <label className="mt-4 block text-[13px] font-bold text-ink-secondary">Phone number</label>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" placeholder="Your phone"
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary" />

            {err && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[13px] font-semibold text-rose-600">{err}</div>}

            <button type="submit" disabled={busy} className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-bold text-white disabled:opacity-60" style={{ background: GRAD }}>
              {busy ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}{busy ? "Searching…" : "Find my repair"}
            </button>
          </form>
          <div className="mt-6 text-center text-[11.5px] text-ink-tertiary">RepairOS</div>
        </div>
      </div>
    </div>
  );
}
