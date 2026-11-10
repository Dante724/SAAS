// Frame timings shared by the picture and the sound design, so every click and ping lands on its frame.
export const HOOK_FRAMES = 60;   // the hook scene: flash-forward + rewind (= HOOK_SEC in captions.ts)
// Eleven clicks, as the line says (frames from the start of the old-software scene).
export const oldSoftwareClicks = (short: boolean) => Array.from({ length: 11 }).map((_, i) => (short ? 6 + i * 5 : 8 + i * 7));
export const OLD_LOCK_SEC = 4.2;     // "Texting is available on a higher plan."
export const APPROVED_IN_SCENE = { "90": 7.4, "30": 5.0, "15": 2.4 } as const;  // seconds into the approval scene
