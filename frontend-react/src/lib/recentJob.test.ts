import { describe, it, expect, beforeEach, vi } from "vitest";
import { rememberJob, recentJob, forgetJob, RECENT_JOB_TTL } from "./recentJob";

const J = { id: "o1", number: "WO-1042", client: "Jane Kim", device: "iPhone 14 Pro" };

describe("recentJob", () => {
  beforeEach(() => { try { localStorage.clear(); } catch { /* storage may be off */ } });

  it("keeps the last job per user for ten minutes", () => {
    rememberJob("u1", J, 1_000);
    expect(recentJob("u1", 1_000 + RECENT_JOB_TTL)).toMatchObject(J);
    expect(recentJob("u2", 1_000)).toBeNull();                      // another person on this browser
    expect(recentJob("u1", 1_000 + RECENT_JOB_TTL + 1)).toBeNull();   // expired...
    expect(localStorage.getItem("tekly.recentJob.u1")).toBeNull();     // ...and cleared
  });

  it("forgets only the job it was asked to", () => {
    rememberJob("u1", J);
    forgetJob("u1", "other");
    expect(recentJob("u1")).not.toBeNull();
    forgetJob("u1", "o1");
    expect(recentJob("u1")).toBeNull();
  });

  it("bad or missing data, no user, or storage that throws: nothing, never an error", () => {
    localStorage.setItem("tekly.recentJob.u1", "{not json");
    expect(recentJob("u1")).toBeNull();
    expect(recentJob(undefined)).toBeNull();
    rememberJob(null, J);
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(recentJob("u1")).toBeNull();
    spy.mockRestore();
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
    expect(() => rememberJob("u1", J)).not.toThrow();
    set.mockRestore();
  });
});
