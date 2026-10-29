// Scan a label (work order, part, device serial/IMEI/VIN, or a ticket's tracking QR) and jump to it.
// Camera first; typing or a Bluetooth/USB keyboard-wedge scanner works in the box below.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Camera, CameraOff, Keyboard, Loader2, SearchX } from "lucide-react";
import { api } from "../../api/client";
import { Card, Page, PrimaryButton } from "../components/ui";
import { routeScan, type ScanResolve } from "../lib/scanRoute";
import { cameraSupported, haptic, startScanner, type Stop } from "../lib/scanner";

type State = { kind: "idle" } | { kind: "camera" } | { kind: "looking"; code: string } | { kind: "miss"; code: string } | { kind: "error"; message: string };

export default function Scan() {
  const nav = useNavigate();
  const video = useRef<HTMLVideoElement>(null);
  const stopRef = useRef<Stop | null>(null);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [manual, setManual] = useState("");

  async function lookup(raw: string) {
    setState({ kind: "looking", code: raw.trim() });
    try {
      const t = await routeScan(raw, (code) => api.post<ScanResolve>("/scan/resolve", { code }));
      if (t.kind === "none") { setState({ kind: "miss", code: t.code }); return; }
      haptic();
      nav(t.path);
    } catch (e: any) {
      setState({ kind: "error", message: navigator.onLine ? (e?.message || "Lookup failed") : "You're offline. Scanning needs a connection to look the code up." });
    }
  }

  async function startCamera() {
    if (!video.current) return;
    setState({ kind: "camera" });
    try {
      stopRef.current = await startScanner(video.current, (code) => { stopRef.current = null; lookup(code); });
    } catch (e: any) {
      const denied = e?.name === "NotAllowedError" || e?.name === "SecurityError";
      setState({ kind: "error", message: denied ? "Camera access is off. Allow the camera for Tekly in Settings, or type the code below." : "Couldn't start the camera. Type the code below instead." });
    }
  }

  useEffect(() => {
    if (cameraSupported()) startCamera();
    return () => { stopRef.current?.(); stopRef.current = null; };
  }, []);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!manual.trim()) return;
    stopRef.current?.(); stopRef.current = null;
    lookup(manual);
  }

  const cameraOn = state.kind === "camera";
  return (
    <Page title="Scan" subtitle="Work order, part, or a device's serial, IMEI or VIN">
      <div className="relative aspect-[4/3] md:aspect-video w-full overflow-hidden rounded-xl bg-[#0b1020]">
        <video ref={video} className={`h-full w-full object-cover ${cameraOn ? "" : "opacity-0"}`} playsInline muted />
        {cameraOn && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden>
            <div className="relative h-[46%] w-[74%] rounded-xl border-2 border-white/80 shadow-[0_0_0_9999px_rgba(0,0,0,.35)]">
              <span className="absolute inset-x-4 top-1/2 h-0.5 -translate-y-1/2 rounded bg-sky-400/90" />
            </div>
          </div>
        )}
        {!cameraOn && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center text-white/85">
            {state.kind === "looking" ? <Loader2 size={32} className="animate-spin" /> : state.kind === "error" ? <CameraOff size={32} /> : <Camera size={32} />}
            <p className="text-go-body">
              {state.kind === "looking" ? `Looking up ${state.code}…` : state.kind === "error" ? state.message : "Point the camera at a label"}
            </p>
            {(state.kind === "idle" || state.kind === "error" || state.kind === "miss") && cameraSupported() && (
              <button onClick={startCamera} className="min-h-[2.75rem] rounded-full bg-white/15 px-5 font-semibold text-white active:bg-white/25">
                {state.kind === "idle" ? "Start camera" : "Scan again"}
              </button>
            )}
          </div>
        )}
      </div>

      {state.kind === "miss" && (
        <Card className="mt-4 p-4 flex gap-3 items-start">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-50 text-amber-700"><SearchX size={20} /></span>
          <div className="min-w-0">
            <p className="font-semibold text-ink-primary">No match</p>
            <p className="text-go-sub text-ink-secondary break-all">Nothing in this shop uses <span className="font-mono">{state.code}</span>. Check the label, or type the order number.</p>
          </div>
        </Card>
      )}

      <form onSubmit={submit} className="mt-5">
        <label htmlFor="go-code" className="mb-1.5 flex items-center gap-1.5 px-1 text-go-sub font-semibold text-ink-tertiary"><Keyboard size={14} />Type or use a scanner</label>
        <div className="flex gap-2">
          <input id="go-code" value={manual} onChange={(e) => setManual(e.target.value)} inputMode="text" autoCapitalize="characters" autoCorrect="off" spellCheck={false}
            enterKeyHint="search" placeholder="WO-00123, SKU, serial…"
            className="min-h-[3.25rem] flex-1 min-w-0 rounded-xl border border-slate-200 bg-white px-4 text-go-body text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15" />
          <PrimaryButton type="submit" className="!w-auto px-6" disabled={!manual.trim() || state.kind === "looking"}>Go</PrimaryButton>
        </div>
      </form>
    </Page>
  );
}
