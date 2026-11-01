import { beforeEach, describe, expect, it } from "vitest";
import { activity, initIdle, LOCK_MS, needsRecheck, readStored, RECHECK_MS, sessionKey, tick, verified, writeStored } from "./idle";
import { pinProblem } from "./pin";

const T0 = 1_700_000_000_000;
const min = (n: number) => n * 60_000;

describe("idle rules", () => {
  it("a fresh sign-in needs no PIN", () => {
    const s = initIdle(T0);
    expect(needsRecheck(s, T0)).toBe(false);
    expect(s.locked).toBe(false);
  });

  it("taps keep it fresh", () => {
    let s = initIdle(T0);
    for (let i = 1; i <= 20; i++) s = activity(s, T0 + min(i));   // a tap every minute for 20 minutes
    expect(needsRecheck(s, T0 + min(20))).toBe(false);
    expect(s.locked).toBe(false);
  });

  it("5 minutes idle: money needs the PIN, even after you start tapping again", () => {
    let s = initIdle(T0);
    expect(needsRecheck(s, T0 + RECHECK_MS - 1)).toBe(false);
    expect(needsRecheck(s, T0 + RECHECK_MS)).toBe(true);
    s = activity(s, T0 + min(6));            // first tap after the gap
    expect(s.locked).toBe(false);
    expect(needsRecheck(s, T0 + min(6))).toBe(true);
    s = activity(s, T0 + min(7));
    expect(needsRecheck(s, T0 + min(7))).toBe(true);   // tapping doesn't wash it out
  });

  it("the PIN clears it", () => {
    let s = activity(initIdle(T0), T0 + min(6));
    s = verified(s, T0 + min(6));
    expect(needsRecheck(s, T0 + min(6))).toBe(false);
  });

  it("a timer tick marks it stale at 5 minutes and locks at 10", () => {
    let s = initIdle(T0);
    s = tick(s, T0 + min(4));
    expect(s.stale).toBe(false);
    s = tick(s, T0 + min(5));
    expect(s.stale).toBe(true);
    expect(s.locked).toBe(false);
    s = tick(s, T0 + LOCK_MS);
    expect(s.locked).toBe(true);
  });

  it("the first tap after 10 minutes locks instead of counting as activity", () => {
    let s = initIdle(T0);
    s = activity(s, T0 + min(11));
    expect(s.locked).toBe(true);
    expect(s.lastActive).toBe(T0);
    // more taps while locked change nothing
    s = activity(s, T0 + min(12));
    expect(s.locked).toBe(true);
  });

  it("time in the background counts: coming back after 12 minutes locks", () => {
    let s = activity(initIdle(T0), T0 + min(1));
    // backgrounded: no ticks fire; the visible event triggers a tick on return
    s = tick(s, T0 + min(13));
    expect(s.locked).toBe(true);
  });

  it("only the PIN unlocks", () => {
    let s = tick(initIdle(T0), T0 + min(15));
    expect(tick(s, T0 + min(16)).locked).toBe(true);
    s = verified(s, T0 + min(16));
    expect(s.locked).toBe(false);
    expect(needsRecheck(s, T0 + min(16))).toBe(false);
  });

  it("a restart remembers the clock: 20 minutes later it opens locked", () => {
    expect(initIdle(T0 + min(20), T0).locked).toBe(true);
    const s = initIdle(T0 + min(6), T0);
    expect(s.locked).toBe(false);
    expect(needsRecheck(s, T0 + min(6))).toBe(true);
    expect(needsRecheck(initIdle(T0 + min(1), T0), T0 + min(1))).toBe(false);
  });

  it("a stored time in the future (clock changed) isn't trusted", () => {
    const s = initIdle(T0, T0 + min(60));
    expect(s.lastActive).toBe(T0);
  });
});

const jwt = (payload: object) => `h.${btoa(JSON.stringify(payload)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_")}.sig`;

describe("stored clock belongs to one sign-in", () => {
  beforeEach(() => localStorage.clear());

  it("keys on the user and original sign-in time (kept by token refresh)", () => {
    expect(sessionKey(jwt({ sub: "e1", auth_time: 100, exp: 1 }))).toBe("e1:100");
    expect(sessionKey(jwt({ sub: "e1", auth_time: 100, exp: 2 }))).toBe("e1:100");
    expect(sessionKey("garbage")).toBeNull();
    expect(sessionKey(null)).toBeNull();
  });

  it("a different sign-in starts fresh", () => {
    writeStored("e1:100", T0);
    expect(readStored("e1:100")).toBe(T0);
    expect(readStored("e1:200")).toBeNull();
    expect(readStored("e2:100")).toBeNull();
    expect(readStored(null)).toBeNull();
  });
});

describe("PIN rules match the server", () => {
  it.each([["2580", true], ["739104", true], ["1357", true], ["123", false], ["1234567", false], ["12a4", false],
    ["1111", false], ["1234", false], ["9876", false], ["456789", false]])("%s ok=%s", (pin, ok) => {
    expect(pinProblem(pin) === null).toBe(ok);
  });
});
