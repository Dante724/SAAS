// Which part of each recording plays in each scene, how fast, and where the camera leans in.
// `from` is seconds into the clip; zoom `t` is seconds into the shot; x/y is the focus point (0..1).
// Beat times are in public/clips/manifest.json (written by capture/record.ts); notes below cite them.
import type { Zoom } from "./ui";

export type Shot = { clip: string; from?: number; rate?: number; zoom?: Zoom[]; fadeOut?: boolean };

const Z = (t: number, s: number, x: number, y: number): Zoom => ({ t, s, x, y });
const WIDE = Z(0, 1, 0.5, 0.5);

export const SHOTS = {
  // photo attached 21.75 · crack circled 25.46 · $140 line 34.62 · WO-00012 on the board 38.45
  intake: { clip: "intake-desktop", from: 21.4, rate: 2.85, zoom: [Z(0, 1.45, 0.5, 0.5), Z(2.4, 1.45, 0.5, 0.5), Z(3.2, 1, 0.5, 0.5), Z(5.1, 1, 0.5, 0.5), Z(5.8, 1.35, 0.32, 0.42)] } as Shot,
  // approval page 1.2 · photo 3.7 · battery "Not now" 11.53 · $140 13.55 · signed 20.28 · Approved 25.19 (→ t 8.4)
  approve: { clip: "approve-mobile", from: 1.6, rate: 3.2 } as Shot,
  approvalSwitchAt: 7.6,
  // Approved pill 1.22
  boardApproved: { clip: "board-approved-desktop", from: 0.6, rate: 1, zoom: [WIDE, Z(0.9, 1.7, 0.47, 0.4)] } as Shot,
  // list: 1 in stock, 1 reserved 1.43 · stock by location 7.78 · timer started 15.73 (→ t 3.5 / 7.5)
  stock: { clip: "stock-desktop", from: 0.8, rate: 3.2, zoom: [Z(0, 1.5, 0.64, 0.6), Z(1.5, 1.5, 0.64, 0.6), Z(2.2, 1.6, 0.76, 0.3), Z(3.5, 1.6, 0.76, 0.3), Z(4.1, 1, 0.5, 0.5)] } as Shot,
  // Ready 6.33 · invoice created 8.54 · pay link 11.9 (→ t 4.4)
  invoice: { clip: "invoice-desktop", from: 5.8, rate: 2.2, zoom: [WIDE, Z(2.0, 1, 0.5, 0.5), Z(2.7, 1.55, 0.66, 0.38)] } as Shot,
  // end of day, her job: WO-00012 Maya Chen · Done (paid) 1.59
  eodPaid: { clip: "eod-board-desktop", from: 0.8, rate: 1, zoom: [WIDE, Z(0.4, 1, 0.5, 0.5), Z(1.2, 1.9, 0.42, 0.41)] } as Shot,
  // end of day: mostly Done 1.0 · WO-00012 Done 1.59
  eod: { clip: "eod-board-desktop", from: 0.8, rate: 1, zoom: [Z(0, 1.2, 0.5, 0.42), Z(3.2, 1, 0.5, 0.5)] } as Shot,
  // the rest of the day (beats: see manifest)
  // details typed 14.2 · signed 19.9 · "You're checked in, Maya · #1" 21.3 (→ t 2.9)
  checkin: { clip: "checkin-mobile", from: 14.8, rate: 3 } as Shot,
  // service 2.4 · tomorrow 3.9 · 10:00 5.9 · confirm 11.8 · "You're booked!" 14.0 (→ t 3.3)
  booking: { clip: "booking-mobile", from: 4.0, rate: 4.2 } as Shot,
  // Scheduler: her 10:00 card 5.8
  bookingDesk: { clip: "booking-desktop", from: 6.0, rate: 1, zoom: [Z(0, 1.5, 0.33, 0.3)] } as Shot,
  // "Apply plan to 2 lines" 15.9 · Diagnostic $35 → $0 covered by plan 19.1
  memberships: { clip: "memberships-desktop", from: 15.4, rate: 1.4, zoom: [Z(0, 1.5, 0.8, 0.36), Z(0.9, 1.5, 0.8, 0.36), Z(1.8, 1.6, 0.68, 0.72)] } as Shot,
  // Provided services chart, last 30 days: every trade's jobs in one list 6.3
  reports: { clip: "reports-desktop", from: 6.0, rate: 1, zoom: [WIDE, Z(0.6, 1, 0.5, 0.5), Z(1.8, 1.45, 0.66, 0.56)] } as Shot,
  // 30 s cut
  approve30: { clip: "approve-mobile", from: 3.7, rate: 4.2 } as Shot,
  stock30: { clip: "stock-desktop", from: 0.9, rate: 1, zoom: [Z(0, 1.5, 0.64, 0.6)] } as Shot,
  invoice30: { clip: "invoice-desktop", from: 10.5, rate: 1.4, zoom: [WIDE, Z(0.8, 1.55, 0.66, 0.38)] } as Shot,
  // 15 s cut
  approve15: { clip: "approve-mobile", from: 3.7, rate: 5.4 } as Shot,
  boardApproved15: { clip: "board-approved-desktop", from: 0.9, rate: 1, zoom: [Z(0, 1.6, 0.47, 0.4)] } as Shot,
  stock15: { clip: "stock-desktop", from: 0.9, rate: 1, zoom: [Z(0, 1.5, 0.64, 0.6)] } as Shot,
};
