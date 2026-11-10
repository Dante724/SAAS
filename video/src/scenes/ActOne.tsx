// Act one: the old way. Stock photos graded cold, and abstract, unbranded "old software" UI
// (grey menus, nested modals, a loader, a renewal email). No real product is shown or named.
import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame } from "remotion";
import { C, FONT_DISPLAY, FONT_TEXT } from "../brand";
import { Photo, Shutter, clamp, fadeIO, useLayout, Wordmark, ease } from "../ui";

type P = { dur: number };

// ---------- shared bits ----------
const Sticky: React.FC<{ text: string; x: string; y: string; rot: number; size?: number; delayF?: number }> = ({ text, x, y, rot, size = 1, delayF = 0 }) => {
  const f = useCurrentFrame();
  const { u, fps } = useLayout();
  const s = spring({ frame: f - delayF, fps, config: { damping: 14, stiffness: 140 } });
  return (
    <div style={{
      position: "absolute", left: x, top: y, width: 230 * u * size, minHeight: 190 * u * size, padding: 22 * u * size,
      background: C.sticky, color: "#3b3320", transform: `rotate(${rot}deg) scale(${0.9 + 0.1 * s})`, opacity: s,
      boxShadow: "0 18px 30px rgba(0,0,0,.35)", fontFamily: FONT_TEXT, fontWeight: 600, fontStyle: "italic",
      fontSize: 30 * u * size, lineHeight: 1.25, letterSpacing: -0.4 * u,
    }}>{text}</div>
  );
};

const CallCard: React.FC<{ dur: number; script: { at: number; status: string; tone?: "red" | "grey" }[]; title?: string }> = ({ dur, script, title = "Maya Chen" }) => {
  const f = useCurrentFrame();
  const { u, fps, aspect } = useLayout();
  const sec = f / fps;
  const cur = [...script].reverse().find((s) => sec >= s.at) ?? script[0];
  const ringing = cur.status.startsWith("Calling");
  const pulse = ringing ? 0.5 + 0.5 * Math.sin(f / 4) : 0;
  const o = fadeIO(f, dur, 8, 6);
  return (
    <div style={{
      position: "absolute", right: aspect === "wide" ? "7%" : "50%", top: aspect === "tall" ? "30%" : "50%",
      transform: `translate(${aspect === "wide" ? 0 : 50}%, -50%)`, opacity: o,
      width: 420 * u, padding: 30 * u, borderRadius: 34 * u, background: "rgba(18,20,24,.88)", color: "#fff",
      boxShadow: "0 30px 70px rgba(0,0,0,.5)", border: "1px solid rgba(255,255,255,.1)", fontFamily: FONT_TEXT, textAlign: "center",
    }}>
      <div style={{ width: 110 * u, height: 110 * u, margin: "0 auto", borderRadius: 99, background: "#3b3f47", display: "grid", placeItems: "center",
        fontFamily: FONT_DISPLAY, fontWeight: 600, fontSize: 40 * u, boxShadow: `0 0 0 ${pulse * 16 * u}px rgba(255,255,255,${0.12 * (1 - pulse)})` }}>MC</div>
      <div style={{ marginTop: 20 * u, fontFamily: FONT_DISPLAY, fontWeight: 600, fontSize: 36 * u }}>{title}</div>
      <div style={{ marginTop: 8 * u, fontSize: 26 * u, color: cur.tone === "red" ? "#ff8a80" : "#b8bcc6" }}>{cur.status}</div>
      <div style={{ marginTop: 26 * u, display: "flex", justifyContent: "center" }}>
        <div style={{ width: 76 * u, height: 76 * u, borderRadius: 99, background: "#e5484d", display: "grid", placeItems: "center" }}>
          <svg width={38 * u} height={38 * u} viewBox="0 0 24 24"><path d="M3 15c5-5 13-5 18 0l-2 3-4-1v-2a9 9 0 0 0-6 0v2l-4 1z" fill="#fff" /></svg>
        </div>
      </div>
    </div>
  );
};

// ---------- 0:00 cold open ----------
export const ColdOpen: React.FC<P> = ({ dur }) => {
  const f = useCurrentFrame();
  const { u, aspect, fps } = useLayout();
  const blink = Math.floor(f / 12) % 2 === 0;
  return (
    <AbsoluteFill>
      <Photo src="photos/bench-hero-1600.webp" dur={dur} cold from={1.12} to={1.02} ox={45} oy={55} />
      <div style={{ position: "absolute", left: "6%", top: aspect === "tall" ? "8%" : "9%", opacity: interpolate(f, [40, 54], [0, 1], clamp),
        fontFamily: FONT_DISPLAY, color: "#fff", fontWeight: 300, fontSize: 120 * u, letterSpacing: -4 * u, textShadow: "0 6px 30px rgba(0,0,0,.5)" }}>
        7:52 <span style={{ fontSize: 56 * u, fontWeight: 400, letterSpacing: 0 }}>a.m.</span>
      </div>
      <Sticky text="iPad – charge port" x={aspect === "wide" ? "62%" : "50%"} y={aspect === "tall" ? "56%" : "50%"} rot={-6} delayF={50} size={0.8} />
      <Sticky text="Galaxy – back glass – call?" x={aspect === "wide" ? "75%" : "66%"} y={aspect === "tall" ? "62%" : "58%"} rot={5} delayF={60} size={0.8} />
      <Sticky text="11 jobs?!" x={aspect === "wide" ? "68%" : "22%"} y={aspect === "tall" ? "68%" : "68%"} rot={-2} delayF={70} size={0.8} />
      {/* the landline's message light */}
      <div style={{ position: "absolute", left: "6%", bottom: aspect === "tall" ? "28%" : "12%", opacity: interpolate(f, [60, 70], [0, 1], clamp),
        display: "flex", alignItems: "center", gap: 14 * u, padding: `${12 * u}px ${20 * u}px`, borderRadius: 14 * u, background: "rgba(10,12,16,.75)",
        color: "#e6e7ea", fontFamily: FONT_TEXT, fontSize: 24 * u }}>
        <span style={{ width: 16 * u, height: 16 * u, borderRadius: 99, background: blink ? "#ff3b30" : "#5a1d1a", boxShadow: blink ? "0 0 18px #ff3b30" : "none" }} />
        Landline · messages waiting
      </div>
      <Shutter startF={4} durF={Math.round(fps * 1.2)} />
    </AbsoluteFill>
  );
};

// ---------- 0:06 the customer (also: 30s redial, 15s calls) ----------
export const Customer: React.FC<P & { variant?: "90" | "30" | "15" }> = ({ dur, variant = "90" }) => {
  const { aspect } = useLayout();
  const script = variant === "90"
    ? [{ at: 0, status: "Calling…" }, { at: 3, status: "No answer", tone: "red" as const }, { at: 4.2, status: "Calling…" }, { at: 5.5, status: "Voicemail", tone: "red" as const }]
    : variant === "30"
      ? [{ at: 0, status: "Calling… (3rd time)" }, { at: 3.2, status: "No answer", tone: "red" as const }]
      : [{ at: 0, status: "Calling… (3rd time)" }, { at: 1.6, status: "No answer", tone: "red" as const }];
  return (
    <AbsoluteFill>
      <Photo src="photos/repair-lab-1-960.webp" dur={dur} cold from={1.05} to={1.16} ox={60} oy={30} />
      <Sticky text="Maya – screen – $140?" x={aspect === "wide" ? "8%" : "8%"} y={aspect === "tall" ? "60%" : "56%"} rot={-4} size={1.15} />
      <CallCard dur={dur} script={script} />
    </AbsoluteFill>
  );
};

// ---------- 0:14 the old software (abstract, unbranded) ----------
const OldWindow: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const { u } = useLayout();
  return (
    <div style={{ position: "absolute", inset: "7% 5%", background: C.grey5, borderRadius: 6 * u, overflow: "hidden", border: `2px solid ${C.grey3}`,
      boxShadow: "0 30px 80px rgba(0,0,0,.5)", fontFamily: "Arial, sans-serif" }}>
      <div style={{ height: 40 * u, background: C.grey2, borderBottom: `2px solid ${C.grey3}`, display: "flex", alignItems: "center", gap: 26 * u, paddingLeft: 20 * u }}>
        {["File", "Edit", "View", "Records", "Tools", "Window", "Help"].map((m) => <span key={m} style={{ fontSize: 18 * u, color: "#555" }}>{m}</span>)}
      </div>
      <div style={{ display: "flex", height: "100%" }}>
        <div style={{ width: "18%", background: "#e2e3e5", borderRight: `2px solid ${C.grey3}`, padding: 16 * u }}>
          {Array.from({ length: 12 }).map((_, i) => <div key={i} style={{ height: 16 * u, margin: `${14 * u}px 0`, width: `${55 + ((i * 37) % 40)}%`, background: C.grey3, borderRadius: 3 }} />)}
        </div>
        <div style={{ flex: 1, padding: 24 * u }}>
          {Array.from({ length: 10 }).map((_, i) => (
            <div key={i} style={{ display: "flex", gap: 12 * u, margin: `${12 * u}px 0` }}>
              {[22, 30, 18, 14].map((wd, k) => <div key={k} style={{ height: 18 * u, width: `${wd}%`, background: k === 0 ? C.grey3 : "#dcdde0", borderRadius: 3 }} />)}
            </div>
          ))}
        </div>
      </div>
      {children}
    </div>
  );
};

const Modal: React.FC<{ i: number; title: string; lockLine?: string; showF: number }> = ({ i, title, lockLine, showF }) => {
  const f = useCurrentFrame();
  const { u } = useLayout();
  if (f < showF) return null;
  const p = interpolate(f, [showF, showF + 5], [0.94, 1], clamp);
  return (
    <div style={{
      position: "absolute", left: `${18 + i * 4}%`, top: `${14 + i * 6}%`, width: "48%", background: "#f6f6f6", border: `2px solid ${C.grey4}`,
      boxShadow: "0 16px 40px rgba(0,0,0,.35)", transform: `scale(${p})`, transformOrigin: "top left", fontFamily: "Arial, sans-serif",
    }}>
      <div style={{ height: 34 * u, background: "#cfd1d5", display: "flex", alignItems: "center", padding: `0 ${14 * u}px`, fontSize: 17 * u, color: "#333", justifyContent: "space-between" }}>
        <span>{title}</span><span>✕</span>
      </div>
      <div style={{ padding: 20 * u }}>
        {lockLine ? (
          <div style={{ display: "flex", alignItems: "center", gap: 14 * u, fontSize: 22 * u, color: "#333" }}>
            <svg width={30 * u} height={30 * u} viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2" fill="#777" /><path d="M8 11V8a4 4 0 0 1 8 0v3" stroke="#777" strokeWidth="2.4" fill="none" /></svg>
            {lockLine}
          </div>
        ) : Array.from({ length: 4 }).map((_, k) => <div key={k} style={{ height: 14 * u, width: `${90 - k * 15}%`, background: "#d6d7da", margin: `${10 * u}px 0` }} />)}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 * u, marginTop: 18 * u }}>
          {(lockLine ? ["Upgrade", "Close"] : ["OK", "Cancel"]).map((b) => <span key={b} style={{ fontSize: 16 * u, padding: `${6 * u}px ${18 * u}px`, border: "1px solid #999", background: "#e8e8e8" }}>{b}</span>)}
        </div>
      </div>
    </div>
  );
};

const Cursor: React.FC<{ path: { f: number; x: number; y: number; click?: boolean }[] }> = ({ path }) => {
  const f = useCurrentFrame();
  const { u, w, h } = useLayout();
  let x = path[0].x, y = path[0].y;
  for (let i = 0; i < path.length - 1; i++) {
    const a = path[i], b = path[i + 1];
    if (f >= a.f && f <= b.f) { const p = ease((f - a.f) / (b.f - a.f)); x = a.x + (b.x - a.x) * p; y = a.y + (b.y - a.y) * p; }
    else if (f > b.f) { x = b.x; y = b.y; }
  }
  const lastClick = [...path].reverse().find((p) => p.click && f >= p.f);
  const ring = lastClick ? interpolate(f - lastClick.f, [0, 10], [0, 1], clamp) : 1;
  return (
    <div style={{ position: "absolute", left: x * w, top: y * h }}>
      {ring < 1 && <div style={{ position: "absolute", left: -22 * u * ring, top: -22 * u * ring, width: 44 * u * ring, height: 44 * u * ring, borderRadius: 99, border: `3px solid rgba(255,255,255,${1 - ring})` }} />}
      <svg width={34 * u} height={34 * u} viewBox="0 0 24 24" style={{ filter: "drop-shadow(0 2px 3px rgba(0,0,0,.5))" }}>
        <path d="M4 2l15 11-6.5 1 3.5 7-3 1.4-3.4-7L4 20z" fill="#fff" stroke="#111" strokeWidth="1.3" />
      </svg>
    </div>
  );
};

export const OldSoftware: React.FC<P & { short?: boolean }> = ({ dur, short }) => {
  const f = useCurrentFrame();
  const { u, fps } = useLayout();
  const step = short ? 8 : 16;                     // frames between modals
  const n = short ? 6 : 5;
  const titles = ["Job record", "Edit job record", "Customer lookup", "Confirm", "Are you sure?", "Record locked"];
  const clicks = Array.from({ length: n }).map((_, i) => ({ f: 10 + i * step, x: 0.36 + i * 0.045, y: 0.44 + i * 0.06, click: true }));
  const loaderF = short ? 999 : Math.round(fps * 4.6);
  const lockF = short ? 999 : Math.round(fps * 6.6);
  const spin = (f * 12) % 360;
  return (
    <AbsoluteFill style={{ background: "#2b2e34" }}>
      <OldWindow>
        {titles.slice(0, n).map((t, i) => <Modal key={i} i={i} title={t} showF={10 + i * step + 3} />)}
        {f >= loaderF && f < lockF && (
          <AbsoluteFill style={{ background: "rgba(0,0,0,.25)", alignItems: "center", justifyContent: "center" }}>
            <div style={{ background: "#f0f0f0", border: "2px solid #999", padding: `${24 * u}px ${36 * u}px`, display: "flex", alignItems: "center", gap: 18 * u, fontFamily: "Arial", fontSize: 24 * u, color: "#333" }}>
              <div style={{ width: 34 * u, height: 34 * u, borderRadius: 99, border: `${5 * u}px solid #ccc`, borderTopColor: "#666", transform: `rotate(${spin}deg)` }} />
              Loading… please wait
            </div>
          </AbsoluteFill>
        )}
        {f >= lockF && <Modal i={1} title="Text messages" lockLine="Texting is available on a higher plan." showF={lockF} />}
      </OldWindow>
      <Cursor path={[{ f: 0, x: 0.3, y: 0.3 }, ...clicks, { f: dur, x: 0.6, y: 0.62 }]} />
      <AbsoluteFill style={{ background: "rgba(10,14,22,.18)", pointerEvents: "none" }} />
      {short && <RenewalEmail showF={Math.round(fps * 1.6)} dur={dur} />}
    </AbsoluteFill>
  );
};

// ---------- 0:24 the drawer ----------
const Crack: React.FC = () => (
  <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
    <g stroke="rgba(255,255,255,.85)" strokeWidth=".35" fill="none">
      <path d="M70 0 L64 18 L72 30 L58 46 L66 60 L52 100" /><path d="M64 18 L40 22 L20 12" /><path d="M58 46 L80 52 L100 48" />
      <path d="M72 30 L90 26" /><path d="M66 60 L44 70 L30 90" /><path d="M40 22 L36 40" />
    </g>
  </svg>
);

export const Drawer: React.FC<P> = ({ dur }) => {
  const f = useCurrentFrame();
  const { u, fps, aspect } = useLayout();
  const open = interpolate(f, [6, 30], [0, 1], { ...clamp, easing: ease });
  const tabIn = spring({ frame: f - Math.round(fps * 2.6), fps, config: { damping: 16 } });
  const flag = f > fps * 5.4;
  const wide = aspect === "wide";
  const cellW = wide ? 760 * u : 860 * u;
  return (
    <AbsoluteFill>
      <Photo src="photos/tools-wall-960.webp" dur={dur} cold dim={0.35} from={1.06} to={1.12} />
      {/* the parts drawer: the iPhone 13 screen bin is empty */}
      <div style={{ position: "absolute", left: wide ? "6%" : "50%", top: wide ? "50%" : "28%", transform: `translate(${wide ? 0 : -50}%, -50%)`,
        width: cellW, background: "#4a4e55", borderRadius: 10 * u, padding: 18 * u, boxShadow: "0 30px 60px rgba(0,0,0,.55)" }}>
        <div style={{ height: 50 * u, background: "#5b6068", borderRadius: 6 * u, marginBottom: 14 * u * open, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ width: 120 * u, height: 14 * u, borderRadius: 8, background: "#8a9099" }} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 * u, height: 300 * u * open, overflow: "hidden" }}>
          {["iPhone 12 screen", "iPhone 13 screen", "iPhone 14 screen", "Batteries", "Charge ports", "Adhesive"].map((l, i) => (
            <div key={l} style={{ background: "#2c2f34", borderRadius: 6 * u, padding: 12 * u, position: "relative", minHeight: 130 * u,
              outline: i === 1 && flag ? `${3 * u}px solid #ff6b5e` : "none" }}>
              <div style={{ fontFamily: FONT_TEXT, fontSize: 18 * u, color: "#333", background: "#e9e6da", padding: `${4 * u}px ${8 * u}px`, display: "inline-block", borderRadius: 3 }}>{l}</div>
              {i !== 1 && Array.from({ length: 2 + (i % 2) }).map((_, k) => <div key={k} style={{ height: 14 * u, margin: `${10 * u}px ${6 * u}px 0`, background: "#7d838b", borderRadius: 3 }} />)}
              {i === 1 && <div style={{ position: "absolute", bottom: 14 * u, left: 0, right: 0, textAlign: "center", fontFamily: FONT_TEXT, fontSize: 22 * u, color: "#8d929b", fontStyle: "italic" }}>empty</div>}
            </div>
          ))}
        </div>
      </div>
      {/* the spreadsheet, on a cracked tablet */}
      <div style={{ position: "absolute", right: wide ? "6%" : "50%", top: wide ? "50%" : "70%", transform: `translate(${wide ? 0 : 50}%, -50%) rotate(${2 - tabIn * 1}deg) translateY(${(1 - tabIn) * 60 * u}px)`,
        opacity: tabIn, width: wide ? 700 * u : 820 * u, padding: 22 * u, background: "#15171b", borderRadius: 36 * u, boxShadow: "0 40px 80px rgba(0,0,0,.6)" }}>
        <div style={{ position: "relative", background: "#f4f5f6", borderRadius: 14 * u, overflow: "hidden", fontFamily: "Arial, sans-serif" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 120px 120px".replace(/120px/g, `${120 * u}px`), fontSize: 22 * u }}>
            {["Part", "Qty", "Bin", "iPhone 12 screen", "2", "A1", "iPhone 13 screen", "1", "A2", "iPhone 14 screen", "3", "A3", "Battery (13)", "4", "B1", "Charge port (C)", "6", "B2"].map((c, i) => {
              const header = i < 3;
              const liar = i === 7;
              return (
                <div key={i} style={{ padding: `${12 * u}px ${14 * u}px`, borderRight: "1px solid #d4d6da", borderBottom: "1px solid #d4d6da",
                  background: header ? "#e1e3e6" : liar && flag ? "#ffe1dd" : "#fff", fontWeight: header || (liar && flag) ? 700 : 400, color: liar && flag ? "#c0392b" : "#333" }}>{c}</div>
              );
            })}
          </div>
          <Crack />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------- 0:32 support and the bill ----------
const RenewalEmail: React.FC<{ showF: number; dur: number }> = ({ showF, dur }) => {
  const f = useCurrentFrame();
  const { u, fps, aspect } = useLayout();
  const s = spring({ frame: f - showF, fps, config: { damping: 16, stiffness: 120 } });
  if (f < showF) return null;
  return (
    <div style={{ position: "absolute", left: "50%", top: aspect === "tall" ? "62%" : "56%", width: 760 * u, transform: `translate(-50%, -50%) translateY(${(1 - s) * 80 * u}px)`, opacity: Math.min(s, fadeIO(f, dur, 0, 6)),
      background: "#fff", borderRadius: 18 * u, boxShadow: "0 40px 90px rgba(0,0,0,.55)", overflow: "hidden", fontFamily: FONT_TEXT }}>
      <div style={{ padding: `${18 * u}px ${26 * u}px`, borderBottom: "1px solid #eee", display: "flex", gap: 16 * u, alignItems: "center", color: "#555", fontSize: 20 * u }}>
        <div style={{ width: 40 * u, height: 40 * u, borderRadius: 99, background: "#c9ccd2" }} />
        <div><div style={{ fontWeight: 600, color: "#222" }}>Billing</div><div>noreply@billing</div></div>
      </div>
      <div style={{ padding: 26 * u }}>
        <div style={{ fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: 38 * u, color: "#1f2128" }}>Your renewal: <span style={{ color: "#d64535" }}>+22%</span></div>
        <div style={{ marginTop: 14 * u, height: 14 * u, width: "86%", background: "#e5e6e9", borderRadius: 4 }} />
        <div style={{ marginTop: 10 * u, height: 14 * u, width: "64%", background: "#e5e6e9", borderRadius: 4 }} />
      </div>
    </div>
  );
};

export const Support: React.FC<P> = ({ dur }) => {
  const f = useCurrentFrame();
  const { u, fps, aspect } = useLayout();
  const bars = Array.from({ length: 5 }).map((_, i) => 0.35 + 0.65 * Math.abs(Math.sin(f / 5 + i)));
  const chatIn = spring({ frame: f - Math.round(fps * 1.2), fps, config: { damping: 16 } });
  const holdOut = interpolate(f, [fps * 2.6, fps * 3], [1, 0], clamp);
  return (
    <AbsoluteFill style={{ background: "#202328" }}>
      <AbsoluteFill style={{ opacity: holdOut }}>
        <div style={{ position: "absolute", left: "50%", top: aspect === "tall" ? "30%" : "34%", transform: "translate(-50%,-50%)", textAlign: "center", color: "#c7cad1", fontFamily: FONT_TEXT }}>
          <div style={{ display: "flex", gap: 10 * u, justifyContent: "center", alignItems: "flex-end", height: 70 * u }}>
            {bars.map((b, i) => <div key={i} style={{ width: 14 * u, height: 70 * u * b, background: "#6b717c", borderRadius: 4 }} />)}
          </div>
          <div style={{ marginTop: 22 * u, fontSize: 32 * u }}>Please hold. Your call is important to us.</div>
        </div>
        <div style={{ position: "absolute", left: "50%", top: aspect === "tall" ? "50%" : "62%", transform: `translate(-50%, ${(1 - chatIn) * 40 * u}px)`, opacity: chatIn,
          display: "flex", gap: 14 * u, alignItems: "flex-end", marginLeft: -260 * u }}>
          <div style={{ width: 52 * u, height: 52 * u, borderRadius: 99, background: "#4b515c", flexShrink: 0 }} />
          <div style={{ background: "#3a3f48", color: "#e7e9ee", padding: `${18 * u}px ${24 * u}px`, borderRadius: `${22 * u}px ${22 * u}px ${22 * u}px ${6 * u}px`,
            fontFamily: FONT_TEXT, fontSize: 28 * u, maxWidth: 560 * u }}>I'm an AI assistant… How can I help you today?</div>
        </div>
      </AbsoluteFill>
      <RenewalEmail showF={Math.round(fps * 2.7)} dur={dur} />
    </AbsoluteFill>
  );
};

// ---------- 0:38 the turn: black, shutter again, Tekly ----------
export const Turn: React.FC<P> = ({ dur }) => {
  const f = useCurrentFrame();
  const { u, fps } = useLayout();
  const black = Math.round(fps * 0.45);
  const up = Math.round(fps * 1.1);
  const markIn = spring({ frame: f - black - up + 6, fps, config: { damping: 18, stiffness: 120 } });
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <AbsoluteFill style={{ background: `radial-gradient(100% 80% at 50% 40%, #ffffff 0%, ${C.surface} 70%, #e6ecf8 100%)` }}>
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity: markIn, transform: `scale(${0.94 + 0.06 * markIn})` }}>
          <Wordmark size={130 * u} />
        </AbsoluteFill>
      </AbsoluteFill>
      <Shutter startF={black} durF={up} />
      <AbsoluteFill style={{ background: "#000", opacity: interpolate(f, [0, black - 2, black + 4], [1, 1, 0], clamp) }} />
    </AbsoluteFill>
  );
};
