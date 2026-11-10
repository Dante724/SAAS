// "The rest of the day" (1:12–1:28): four quick beats every kind of repair shop knows —
// walk-ins, bookings, regulars, month end — each a real recording of the app.
import React from "react";
import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame } from "remotion";
import { C, FONT_DISPLAY, FONT_TEXT } from "../brand";
import { SHOTS } from "../shots";
import { Float3D } from "../cinema";
import { Stage, TeklyMark, clamp, useLayout } from "../ui";
import { DesktopShot, MobileShot, hasClip } from "./ActTwo";

type P = { dur: number };

// The counter sign: the shop's real check-in QR on an acrylic stand.
const QRSign: React.FC<{ size: number }> = ({ size }) => {
  const f = useCurrentFrame();
  const { u } = useLayout();
  const scan = interpolate(f, [16, 30], [0, 1], clamp);
  return (
    <div style={{ width: size, padding: size * 0.07, borderRadius: size * 0.06, background: "linear-gradient(180deg,#ffffff,#f3f6fb)",
      boxShadow: "0 40px 80px rgba(16,24,64,.25), inset 0 0 0 1px rgba(16,24,64,.08)", textAlign: "center", fontFamily: FONT_TEXT }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10 * u, color: C.sapphire, fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: size * 0.085 }}>
        <TeklyMark size={size * 0.09} /> Check in here
      </div>
      <div style={{ color: C.inkMuted, fontSize: size * 0.05, marginTop: size * 0.01 }}>Dani's Repair · Front counter</div>
      <div style={{ position: "relative", margin: `${size * 0.05}px auto 0`, width: size * 0.72, height: size * 0.72 }}>
        <Img src={staticFile("clips/checkin-qr.png")} style={{ width: "100%", height: "100%", imageRendering: "pixelated" }} />
        {scan > 0 && scan < 1 && <div style={{ position: "absolute", left: -6, right: -6, top: `${scan * 100}%`, height: 4 * u, background: C.sapphireLight, boxShadow: `0 0 ${18 * u}px ${C.sapphireLight}` }} />}
        {[0, 1, 2, 3].map((k) => (
          <div key={k} style={{ position: "absolute", width: size * 0.12, height: size * 0.12, opacity: interpolate(f, [12, 16], [0, 1], clamp),
            [k < 2 ? "top" : "bottom"]: -size * 0.03, [k % 2 ? "right" : "left"]: -size * 0.03,
            borderTop: k < 2 ? `${4 * u}px solid ${C.sapphireLight}` : undefined, borderBottom: k >= 2 ? `${4 * u}px solid ${C.sapphireLight}` : undefined,
            borderLeft: k % 2 ? undefined : `${4 * u}px solid ${C.sapphireLight}`, borderRight: k % 2 ? `${4 * u}px solid ${C.sapphireLight}` : undefined,
            borderRadius: 6 * u } as React.CSSProperties} />
        ))}
      </div>
      <div style={{ color: C.ink, fontSize: size * 0.045, marginTop: size * 0.05 }}>Scan with your phone camera.<br />We'll text you when it's your turn.</div>
    </div>
  );
};

export const CheckIn: React.FC<P> = ({ dur }) => {
  const f = useCurrentFrame();
  const { u, aspect, fps } = useLayout();
  const signIn = spring({ frame: f, fps, config: { damping: 20, stiffness: 120 } });
  const wide = aspect === "wide";
  return (
    <Stage photo="photos/repair-lab-1-960.webp">
      <div style={{ position: "absolute", left: wide ? "30%" : "50%", top: wide ? "50%" : aspect === "tall" ? "24%" : "47%",
        transform: `translate(-50%, -50%) translateY(${(1 - signIn) * 40 * u}px) ${wide ? "" : aspect === "square" ? "translateX(-28%) scale(.72)" : "scale(.66)"}`, opacity: signIn }}>
        <Float3D dur={dur} from={10} to={4} tiltX={3} seed={11}><QRSign size={520 * u} /></Float3D>
      </div>
      <MobileShot shot={SHOTS.checkin} dur={dur} x={wide ? "68%" : aspect === "square" ? "70%" : "50%"} y={aspect === "tall" ? "69%" : "52%"}
        height={aspect === "tall" ? 860 : aspect === "square" ? 800 : 860} delay={8} />
    </Stage>
  );
};

// Booked overnight: the shop at night, the booking landing anyway.
export const Booking: React.FC<P> = ({ dur }) => {
  const { aspect } = useLayout();
  const wide = aspect === "wide";
  return (
    <AbsoluteFill>
      <Stage photo="photos/workshop-garage-960.webp">
        <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(10,20,52,.82), rgba(16,30,72,.7))" }} />
        {wide && hasClip("booking-desktop") && <DesktopShot shot={SHOTS.bookingDesk} dur={dur} scale={0.72} dx={-230} dy={10} />}
        <MobileShot shot={SHOTS.booking} dur={dur} x={wide ? "74%" : "50%"} height={aspect === "tall" ? 1240 : 860} delay={wide ? 6 : 0} />
      </Stage>
    </AbsoluteFill>
  );
};

export const Membership: React.FC<P> = ({ dur }) => <Stage><DesktopShot shot={SHOTS.memberships} dur={dur} /></Stage>;
export const Reports: React.FC<P> = ({ dur }) => <Stage><DesktopShot shot={SHOTS.reports} dur={dur} /></Stage>;

// The close: the same check-in, as four different kinds of shop see it.
// Each shop's page wears its own brand colour; frames are taken once the trade's own fields show.
const TRADES = [
  { poster: "clips/checkin-mobile.png", label: "Phones & devices", shop: "Dani's Repair" },
  { poster: "clips/checkin-auto-mobile-trade.jpg", label: "Auto", shop: "Northside Auto" },
  { poster: "clips/checkin-engine-mobile-trade.jpg", label: "Small engines", shop: "Ridge Small Engine" },
  { poster: "clips/checkin-marine-mobile-trade.jpg", label: "Marine", shop: "Harbor Marine" },
];
export const TradeFan: React.FC<{ dur: number }> = ({ dur }) => {
  const f = useCurrentFrame();
  const { u, fps, aspect } = useLayout();
  const out = interpolate(f, [dur - 12, dur], [1, 0], clamp);
  const h = (aspect === "wide" ? 760 : aspect === "square" ? 560 : 620) * u;
  const gap = (aspect === "wide" ? 420 : aspect === "square" ? 262 : 250) * u;
  const cols = aspect === "tall" ? 2 : 4;
  return (
    <Stage>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity: out }}>
        {TRADES.map((t, i) => {
          const s = spring({ frame: f - i * 5, fps, config: { damping: 18, stiffness: 120 } });
          const col = i % cols, row = Math.floor(i / cols), rows = Math.ceil(TRADES.length / cols);
          const x = (col - (cols - 1) / 2) * gap * (aspect === "tall" ? 1.7 : 1);
          const y = (row - (rows - 1) / 2) * (h + 110 * u) - 40 * u;
          const rot = (col - (cols - 1) / 2) * (aspect === "tall" ? 3 : 4);
          return (
            <div key={t.label} style={{ position: "absolute", left: "50%", top: "50%", transform: `translate(-50%, -50%) translate(${x}px, ${y + (1 - s) * 80 * u}px) rotate(${rot}deg)`, opacity: s, textAlign: "center" }}>
              <div style={{ width: h * 0.462, height: h, borderRadius: h * 0.08, padding: h * 0.02, background: "#111318", boxShadow: "0 30px 60px rgba(16,24,64,.28)" }}>
                <Img src={staticFile(t.poster)} style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: "top", borderRadius: h * 0.065 }} />
              </div>
              <div style={{ marginTop: 16 * u, fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: (aspect === "square" ? 24 : 30) * u, color: C.ink }}>{t.label}</div>
              <div style={{ fontFamily: FONT_TEXT, fontSize: (aspect === "square" ? 18 : 21) * u, color: C.inkMuted }}>{t.shop}</div>
            </div>
          );
        })}
      </AbsoluteFill>
    </Stage>
  );
};
