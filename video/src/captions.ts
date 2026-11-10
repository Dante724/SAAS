// The script's timeline, copied from "Tekly promo video: story and script" (owner's doc).
// Owner additions (Nov 10): a hook, the shop's QR self check-in, online booking, memberships and
// reports ("the rest of the day", 1:12–1:28), so the 90 s cut now runs 1:48. Everything else below
// mirrors the script's tables
// (owner's change: the product is named in full, "teklyGO", wherever the script says "Tekly"):
// `from`/`to` are the script's timecodes in seconds. Captions are derived from this file only,
// so if the script changes, change it here and every cut and aspect follows.

export type Cut = "90" | "30" | "15";
export type Aspect = "16:9" | "1:1" | "9:16";

export type SceneId =
  // 90
  | "hook" | "coldOpen" | "customer" | "oldSoftware" | "drawer" | "support" | "turn"
  | "intake" | "approval" | "part" | "close" | "danisLine" | "endCard"
  // 30
  | "checkin" | "booking" | "membership" | "reports"
  | "redial" | "clicksRenewal" | "approval30" | "close30" | "endCard30"
  // 15
  | "calls15" | "text15" | "approved15" | "home15" | "end15";

export type OnScreen = { text: string; from: number; to: number; strong?: boolean };

export type Segment = {
  id: string;
  from: number;          // seconds, script timecode
  to: number;            // seconds, script timecode
  scene: SceneId;
  vo?: string;           // the voice-over line, verbatim
  speaker?: "dani" | "narrator";
  onScreen?: OnScreen[]; // the script's "On-screen text" column (absolute seconds)
  bold?: string;          // 15-second cut: the bold caption is the whole message (no VO)
  titleCard?: boolean;    // the line is set as a full-frame title; the title is its caption
};

const t = (m: number, s: number) => m * 60 + s;
// The 90 s cut opens with a 2-second hook (flash-forward + rewind) before the script's 0:00,
// so its script timecodes are offset by HOOK_SEC.
export const HOOK_SEC = 2;
const T = (m: number, s: number) => t(m, s) + HOOK_SEC;

export const TIMELINE: Record<Cut, { duration: number; turnAt: number; segments: Segment[] }> = {
  "90": {
    duration: T(1, 48),
    turnAt: T(0, 38),   // music: the light acoustic rhythm enters exactly on "Same Tuesday"
    segments: [
      { id: "hook", from: 0, to: HOOK_SEC, scene: "hook" },
      { id: "cold-open", from: T(0, 0), to: T(0, 6), scene: "coldOpen", speaker: "dani",
        vo: "Tuesday. Eleven jobs on the board. One of them is going to eat my whole day. I just don't know which one yet." },
      { id: "the-customer", from: T(0, 6), to: T(0, 14), scene: "customer", speaker: "dani",
        vo: "Maya dropped off a phone and ran. Screen's $140. I need a yes before I order the part. So I call. And call.",
        onScreen: [
          { text: "Call 1 – no answer", from: T(0, 9), to: T(0, 11.5) },
          { text: "Call 2 – voicemail", from: T(0, 11.5), to: T(0, 14) },
        ] },
      { id: "old-software", from: T(0, 14), to: T(0, 24), scene: "oldSoftware", speaker: "dani",
        vo: "My software wants eleven clicks to tell me what I already know. And the texting feature? That's on the bigger plan.",
        // the script's "Click. Click. Click…" text is the on-screen click counter
      },
      { id: "the-drawer", from: T(0, 24), to: T(0, 32), scene: "drawer", speaker: "dani",
        vo: "The part I was sure I had, I don't. The spreadsheet says I do. The spreadsheet is a liar." },
      { id: "support-and-bill", from: T(0, 32), to: T(0, 38), scene: "support", speaker: "dani",
        vo: "Support used to be a person. Now it's a bot. And the price went up again, with eleven months left on a plan I already hate.",
        // "Renewal: +22%" is the email's own subject line on screen
      },
      { id: "the-turn", from: T(0, 38), to: T(0, 42), scene: "turn", speaker: "dani",
        vo: "Same Tuesday. One thing different.",
        onScreen: [{ text: "teklyGO", from: T(0, 39.5), to: T(0, 42), strong: true }] },
      { id: "intake", from: T(0, 42), to: T(0, 52), scene: "intake", speaker: "dani",
        vo: "I take the photo, circle the crack, and the job exists. No sticky note. No eleven clicks.",
        onScreen: [{ text: "Intake → Work order", from: T(0, 43), to: T(0, 52) }] },
      { id: "the-approval", from: T(0, 52), to: T(1, 4), scene: "approval", speaker: "dani",
        vo: "Maya gets a text. Photos, price, a thumb to sign. She says yes to the screen and 'not today' to the battery, and I never picked up the phone.",
        onScreen: [{ text: "Approved · 9:14 a.m.", from: T(1, 0.5), to: T(1, 4) }] },
      { id: "the-part", from: T(1, 4), to: T(1, 12), scene: "part", speaker: "dani",
        vo: "The part was reserved the second she approved. The clock starts when I do. Nobody's guessing anymore.",
        onScreen: [{ text: "1 in stock · reserved", from: T(1, 5), to: T(1, 12) }] },
      { id: "walk-in", from: T(1, 12), to: T(1, 16), scene: "checkin", speaker: "dani",
        vo: "Walk-ins scan the code on the counter and check themselves in.",
        onScreen: [{ text: "Self check-in", from: T(1, 12.6), to: T(1, 16) }] },
      { id: "booking", from: T(1, 16), to: T(1, 20), scene: "booking", speaker: "dani",
        vo: "Tomorrow books itself while I'm asleep.",
        onScreen: [{ text: "Online booking · 11:48 p.m.", from: T(1, 16.6), to: T(1, 20) }] },
      { id: "membership", from: T(1, 20), to: T(1, 24), scene: "membership", speaker: "dani",
        vo: "My regulars are on a plan, so they keep coming back.",
        onScreen: [{ text: "Memberships", from: T(1, 20.6), to: T(1, 24) }] },
      { id: "reports", from: T(1, 24), to: T(1, 28), scene: "reports", speaker: "dani",
        vo: "And at month end, I know what actually made money.",
        onScreen: [{ text: "Reports", from: T(1, 24.6), to: T(1, 28) }] },
      { id: "close-of-job", from: T(1, 28), to: T(1, 36), scene: "close", speaker: "dani",
        vo: "Done means done. The invoice writes itself, she pays before she parks, and I'm not here at nine tonight.",
        onScreen: [{ text: "Paid · 4:12 p.m.", from: T(1, 32.4), to: T(1, 36) }] },
      { id: "danis-line", from: T(1, 36), to: T(1, 42), scene: "danisLine", speaker: "dani",
        vo: "I didn't need more software. I needed the day back.", titleCard: true },
      { id: "close", from: T(1, 42), to: T(1, 48), scene: "endCard", speaker: "narrator",
        vo: "teklyGO. Repair shop software that gives you the day back. Free trial. Bring your data. No contract.",
        onScreen: [{ text: "tekly.app · Free trial · No contract", from: T(1, 43), to: T(1, 48), strong: true }] },
    ],
  },
  "30": {
    duration: t(0, 30),
    turnAt: t(0, 9),
    segments: [
      { id: "redial", from: t(0, 0), to: t(0, 5), scene: "redial", speaker: "dani",
        vo: "Three calls to get one yes on a $140 screen.",
        onScreen: [{ text: "Call 3…", from: t(0, 0.5), to: t(0, 5) }] },
      { id: "clicks-renewal", from: t(0, 5), to: t(0, 9), scene: "clicksRenewal", speaker: "dani",
        vo: "Software that charges more every year to make me click more.",
        // "Renewal: +22%" is the email's own subject line on screen
      },
      { id: "the-turn", from: t(0, 9), to: t(0, 12), scene: "turn", speaker: "dani",
        vo: "Same Tuesday. One thing different.",
        onScreen: [{ text: "teklyGO", from: t(0, 10), to: t(0, 12), strong: true }] },
      { id: "the-approval", from: t(0, 12), to: t(0, 22), scene: "approval30", speaker: "dani",
        vo: "She gets a text. Photos, price, a thumb to sign. The part's reserved before I've put the phone down.",
        onScreen: [{ text: "Approved · 9:14 a.m.", from: t(0, 17), to: t(0, 22) }] },
      { id: "close-of-job", from: t(0, 22), to: t(0, 26), scene: "close30", speaker: "dani",
        vo: "Done means done. I'm home for dinner.",
        onScreen: [{ text: "Paid", from: t(0, 23.5), to: t(0, 26) }] },
      { id: "close", from: t(0, 26), to: t(0, 30), scene: "endCard30", speaker: "narrator",
        vo: "teklyGO. Free trial. No contract.",
        onScreen: [{ text: "tekly.app", from: t(0, 26.5), to: t(0, 30), strong: true }] },
    ],
  },
  "15": {
    duration: t(0, 15),
    turnAt: t(0, 3),
    segments: [
      { id: "calls", from: t(0, 0), to: t(0, 3), scene: "calls15", bold: "Three calls. Still no yes." },
      { id: "one-text", from: t(0, 3), to: t(0, 7), scene: "text15", bold: "One text. Photos. Price. A thumb." },
      { id: "approved", from: t(0, 7), to: t(0, 10), scene: "approved15", bold: "Approved. Part reserved. Nobody called anyone." },
      { id: "home", from: t(0, 10), to: t(0, 13), scene: "home15", bold: "Home for dinner." },
      { id: "close", from: t(0, 13), to: t(0, 15), scene: "end15", bold: "teklyGO · Free trial · tekly.app" },
    ],
  },
};

export type Cue = { segment: string; from: number; to: number; text: string; kind: "vo" | "bold"; titleCard?: boolean };

// Captions: one cue per sentence. A segment's first cue starts on the script's timecode and its
// last cue ends on the next timecode; the time in between is shared by sentence length.
export const captionCues = (cut: Cut): Cue[] => {
  const out: Cue[] = [];
  for (const s of TIMELINE[cut].segments) {
    if (s.bold) { out.push({ segment: s.id, from: s.from, to: s.to, text: s.bold, kind: "bold" }); continue; }
    if (!s.vo) continue;
    const parts = splitSentences(s.vo);
    const total = parts.reduce((n, p) => n + p.length + 8, 0);
    let at = s.from;
    parts.forEach((p, i) => {
      const len = ((p.length + 8) / total) * (s.to - s.from);
      const end = i === parts.length - 1 ? s.to : at + len;
      out.push({ segment: s.id, from: round(at), to: round(end), text: p, kind: "vo", titleCard: s.titleCard });
      at = end;
    });
  }
  return out;
};

const round = (n: number) => Math.round(n * 1000) / 1000;

// Short sentences ride together so a caption never flashes for less than a second.
const splitSentences = (vo: string): string[] => {
  const raw = vo.match(/[^.?!]+[.?!]+['"]?\s*/g)?.map((x) => x.trim()) ?? [vo];
  const out: string[] = [];
  for (const r of raw) {
    const last = out[out.length - 1];
    if (last && (last.length < 22 || r.length < 14) && last.length + r.length < 70) out[out.length - 1] = `${last} ${r}`;
    else out.push(r);
  }
  return out;
};

export const FPS = 30;
export const SIZES: Record<Aspect, { width: number; height: number }> = {
  "16:9": { width: 1920, height: 1080 },
  "1:1": { width: 1080, height: 1080 },
  "9:16": { width: 1080, height: 1920 },
};
